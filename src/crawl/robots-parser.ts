/**
 * Parse robots.txt for Allow/Disallow rules and Sitemap directives.
 *
 * Path patterns follow RFC 9309 / common crawler semantics:
 * - `*` matches any sequence of characters
 * - a trailing `$` anchors the pattern to the end of the path
 * - when multiple rules match, the longest (most specific) pattern wins;
 *   ties are resolved in favour of Allow (the least restrictive rule)
 */
import { logger } from '../logger.js';

export type RobotsRuleType = 'allow' | 'disallow';

export interface RobotsRules {
  disallowPaths: string[];
  allowPaths: string[];
  sitemapUrls: string[];
}

export interface RobotsDecision {
  allowed: boolean;
  /** The pattern that decided the outcome, when a rule matched. */
  rule?: string;
  /** Whether the deciding rule was an Allow or Disallow. */
  ruleType?: RobotsRuleType;
  /** Human-readable explanation, present when the URL is disallowed. */
  reason?: string;
}

/**
 * Parse robots.txt content into structured rules.
 * Extracts Allow/Disallow rules for the wildcard user-agent (*) and all
 * Sitemap directives.
 */
export function parseRobotsTxt(content: string): RobotsRules {
  const disallowPaths: string[] = [];
  const allowPaths: string[] = [];
  const sitemapUrls: string[] = [];
  let inWildcardBlock = false;

  for (const rawLine of content.split('\n')) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) continue;

    const colonIdx = line.indexOf(':');
    if (colonIdx === -1) continue;

    const field = line.slice(0, colonIdx).trim().toLowerCase();
    const value = line.slice(colonIdx + 1).trim();

    if (field === 'user-agent') {
      inWildcardBlock = value === '*';
      continue;
    }

    if (field === 'sitemap' && value) {
      sitemapUrls.push(value);
      continue;
    }

    // An empty Disallow/Allow value means "no restriction"; ignore it.
    if (field === 'disallow' && value && inWildcardBlock) {
      disallowPaths.push(value);
    }

    if (field === 'allow' && value && inWildcardBlock) {
      allowPaths.push(value);
    }
  }

  return { disallowPaths, allowPaths, sitemapUrls };
}

const REGEX_SPECIAL_CHARS = /[.*+?^${}()|[\]\\]/g;

function escapeRegExp(value: string): string {
  return value.replace(REGEX_SPECIAL_CHARS, '\\$&');
}

/**
 * Test whether a URL path matches a robots.txt pattern.
 * `*` matches any sequence of characters; a trailing `$` anchors the
 * pattern to the end of the path. All other characters are literal.
 */
export function matchRobotsPattern(urlPath: string, pattern: string): boolean {
  if (!pattern) return false;

  let body = pattern;
  let endAnchored = false;
  if (body.endsWith('$')) {
    endAnchored = true;
    body = body.slice(0, -1);
  }

  const source = body
    .split('*')
    .map((part) => escapeRegExp(part))
    .join('.*');

  return new RegExp(`^${source}${endAnchored ? '$' : ''}`).test(urlPath);
}

/**
 * Specificity of a pattern for longest-match resolution.
 * The trailing `$` anchor is not part of the matched path, so it is excluded.
 */
function patternSpecificity(pattern: string): number {
  return pattern.endsWith('$') ? pattern.length - 1 : pattern.length;
}

/**
 * Decide whether a URL path is allowed by the given robots.txt rules.
 * The longest matching pattern wins; on a tie, Allow wins (least restrictive).
 */
export function evaluateRobotsRules(
  urlPath: string,
  disallowPaths: string[],
  allowPaths: string[] = []
): RobotsDecision {
  const matches: Array<{ pattern: string; ruleType: RobotsRuleType; specificity: number }> = [];

  for (const pattern of disallowPaths) {
    if (matchRobotsPattern(urlPath, pattern)) {
      matches.push({ pattern, ruleType: 'disallow', specificity: patternSpecificity(pattern) });
    }
  }
  for (const pattern of allowPaths) {
    if (matchRobotsPattern(urlPath, pattern)) {
      matches.push({ pattern, ruleType: 'allow', specificity: patternSpecificity(pattern) });
    }
  }

  if (matches.length === 0) return { allowed: true };

  let best = matches[0];
  for (const candidate of matches.slice(1)) {
    if (candidate.specificity > best.specificity) {
      best = candidate;
    } else if (
      candidate.specificity === best.specificity &&
      candidate.ruleType === 'allow' &&
      best.ruleType === 'disallow'
    ) {
      // Equal specificity: the least restrictive rule (Allow) wins.
      best = candidate;
    }
  }

  if (best.ruleType === 'allow') {
    return { allowed: true, rule: best.pattern, ruleType: best.ruleType };
  }

  return {
    allowed: false,
    rule: best.pattern,
    ruleType: best.ruleType,
    reason: `Blocked by robots.txt Disallow: ${best.pattern}`,
  };
}

/**
 * Check if a URL path is allowed by robots.txt rules.
 */
export function isAllowedByRobots(
  urlPath: string,
  disallowPaths: string[],
  allowPaths: string[] = []
): boolean {
  return evaluateRobotsRules(urlPath, disallowPaths, allowPaths).allowed;
}

/**
 * Fetch and parse robots.txt for a given origin.
 * Returns null if robots.txt is not found or cannot be fetched.
 */
export async function fetchRobotsTxt(
  origin: string,
  fetchFn: (url: string) => Promise<{ ok: boolean; text: string } | null>
): Promise<RobotsRules | null> {
  try {
    const url = `${origin}/robots.txt`;
    const response = await fetchFn(url);
    if (!response?.ok || !response.text) return null;

    const rules = parseRobotsTxt(response.text);
    logger.debug(
      {
        origin,
        disallowCount: rules.disallowPaths.length,
        allowCount: rules.allowPaths.length,
        sitemapCount: rules.sitemapUrls.length,
      },
      'Parsed robots.txt'
    );
    return rules;
  } catch (e) {
    logger.debug({ origin, error: String(e) }, 'Failed to fetch robots.txt');
    return null;
  }
}
