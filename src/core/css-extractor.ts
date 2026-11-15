/**
 * CSS Extractor
 *
 * Extracts and parses CSS from various sources:
 * - Inline styles
 * - <style> tags
 * - External stylesheets
 *
 * Also extracts CSS custom properties (variables).
 */

import css from 'css';
import { fetchStylesheet } from './http-client.js';
import type { StylesheetResult } from '../types/index.js';

/**
 * Maximum number of external stylesheets fetched per extraction.
 *
 * The first MAX_STYLESHEETS discovered links are fetched concurrently; any
 * links beyond this limit are explicitly marked as 'skipped-limit' rather than
 * silently dropped.
 */
export const MAX_STYLESHEETS = 10;

export interface CSSDeclaration {
    property: string;
    value: string;
}

export interface CSSRule {
    selectors: string[];
    declarations: CSSDeclaration[];
}

export interface ExtractedCSS {
    /** All CSS rules */
    rules: CSSRule[];
    /** CSS custom properties (variables) */
    cssVariables: Record<string, string>;
    /** Media queries found */
    mediaQueries: string[];
    /** All unique property-value pairs */
    allDeclarations: CSSDeclaration[];
    /** Per-stylesheet fetch outcomes in discovery order (one entry per discovered link) */
    stylesheetResults: StylesheetResult[];
}

/**
 * Parse CSS content and extract rules
 */
function parseCSSContent(cssContent: string): {
    rules: CSSRule[];
    cssVariables: Record<string, string>;
    mediaQueries: string[];
} {
    const rules: CSSRule[] = [];
    const cssVariables: Record<string, string> = {};
    const mediaQueries: string[] = [];

    try {
        const parsed = css.parse(cssContent, { silent: true });

        if (parsed.stylesheet) {
            processRules(parsed.stylesheet.rules, rules, cssVariables, mediaQueries);
        }
    } catch {
        // Silently handle malformed CSS
    }

    return { rules, cssVariables, mediaQueries };
}

/**
 * Process CSS rules recursively (handles @media, @supports, etc.)
 */
function processRules(
    astRules: css.Rule[] | undefined,
    rules: CSSRule[],
    cssVariables: Record<string, string>,
    mediaQueries: string[]
): void {
    if (!astRules) return;

    for (const rule of astRules) {
        if (rule.type === 'rule' && 'selectors' in rule && 'declarations' in rule) {
            const declarations: CSSDeclaration[] = [];

            for (const decl of rule.declarations || []) {
                if (decl.type === 'declaration' && 'property' in decl && 'value' in decl) {
                    const property = decl.property as string;
                    const value = decl.value as string;

                    declarations.push({ property, value });

                    // Extract CSS variables from :root or html
                    if (
                        property.startsWith('--') &&
                        rule.selectors?.some((s: string) => s === ':root' || s === 'html' || s === 'body')
                    ) {
                        cssVariables[property] = value;
                    }
                }
            }

            if (declarations.length > 0) {
                rules.push({
                    selectors: rule.selectors || [],
                    declarations,
                });
            }
        } else if (rule.type === 'media' && 'media' in rule) {
            const mediaQuery = rule.media as string;
            if (mediaQuery && !mediaQueries.includes(mediaQuery)) {
                mediaQueries.push(mediaQuery);
            }
            // Process nested rules
            if ('rules' in rule) {
                processRules(rule.rules as css.Rule[], rules, cssVariables, mediaQueries);
            }
        } else if (rule.type === 'supports' || rule.type === 'document') {
            // Process nested rules in @supports or @-moz-document
            if ('rules' in rule) {
                processRules(rule.rules as css.Rule[], rules, cssVariables, mediaQueries);
            }
        }
    }
}

/**
 * Parse inline style attribute value
 */
function parseInlineStyle(styleAttr: string): CSSDeclaration[] {
    const declarations: CSSDeclaration[] = [];
    const parts = styleAttr.split(';');

    for (const part of parts) {
        const colonIndex = part.indexOf(':');
        if (colonIndex > 0) {
            const property = part.substring(0, colonIndex).trim().toLowerCase();
            const value = part.substring(colonIndex + 1).trim();
            if (property && value) {
                declarations.push({ property, value });
            }
        }
    }

    return declarations;
}

/**
 * Extract all CSS from multiple sources
 */
export async function extractAllCSS(
    inlineStyles: string[],
    styleTags: string[],
    stylesheetUrls: string[]
): Promise<ExtractedCSS> {
    const allRules: CSSRule[] = [];
    const allCssVariables: Record<string, string> = {};
    const allMediaQueries: string[] = [];
    const allDeclarations: CSSDeclaration[] = [];

    // Process inline styles
    for (const style of inlineStyles) {
        const declarations = parseInlineStyle(style);
        allDeclarations.push(...declarations);

        // Create a pseudo-rule for inline styles
        if (declarations.length > 0) {
            allRules.push({
                selectors: ['[inline-style]'],
                declarations,
            });
        }
    }

    // Process <style> tags
    for (const styleContent of styleTags) {
        const { rules, cssVariables, mediaQueries } = parseCSSContent(styleContent);
        allRules.push(...rules);
        Object.assign(allCssVariables, cssVariables);

        for (const mq of mediaQueries) {
            if (!allMediaQueries.includes(mq)) {
                allMediaQueries.push(mq);
            }
        }

        // Collect declarations
        for (const rule of rules) {
            allDeclarations.push(...rule.declarations);
        }
    }

    // Fetch and process external stylesheets.
    // At most MAX_STYLESHEETS discovered links are fetched, all concurrently
    // (preserving the previous concurrency-cap semantics). Links beyond the
    // cap are explicitly recorded as 'skipped-limit' instead of being dropped.
    const stylesheetResults: StylesheetResult[] = [];

    const fetchedResults = await Promise.all(
        stylesheetUrls.slice(0, MAX_STYLESHEETS).map((url) => fetchStylesheet(url))
    );

    for (const result of fetchedResults) {
        // Record the verifiable outcome (the CSS body is not part of the shared type)
        stylesheetResults.push({
            url: result.url,
            outcome: result.outcome,
            statusCode: result.statusCode,
            ...(result.error ? { error: result.error } : {}),
        });

        // Only successfully fetched stylesheets feed the downstream parsers.
        // A single failed stylesheet must never interrupt the whole extraction.
        if (result.outcome === 'success') {
            const { rules, cssVariables, mediaQueries } = parseCSSContent(result.css);
            allRules.push(...rules);
            Object.assign(allCssVariables, cssVariables);

            for (const mq of mediaQueries) {
                if (!allMediaQueries.includes(mq)) {
                    allMediaQueries.push(mq);
                }
            }

            // Collect declarations
            for (const rule of rules) {
                allDeclarations.push(...rule.declarations);
            }
        }
    }

    // Every link beyond the fetch limit must be visible in the results.
    for (const url of stylesheetUrls.slice(MAX_STYLESHEETS)) {
        stylesheetResults.push({
            url,
            outcome: 'skipped-limit',
            statusCode: 0,
            error: `Stylesheet not fetched: exceeded the limit of ${MAX_STYLESHEETS} stylesheets per extraction`,
        });
    }

    return {
        rules: allRules,
        cssVariables: allCssVariables,
        mediaQueries: allMediaQueries,
        allDeclarations,
        stylesheetResults,
    };
}

/**
 * Extract values for a specific CSS property across all rules
 */
export function getPropertyValues(
    declarations: CSSDeclaration[],
    property: string
): string[] {
    const values: string[] = [];
    const normalizedProp = property.toLowerCase();

    for (const decl of declarations) {
        if (decl.property.toLowerCase() === normalizedProp) {
            if (!values.includes(decl.value)) {
                values.push(decl.value);
            }
        }
    }

    return values;
}

/**
 * Get all values for multiple related properties
 */
export function getRelatedPropertyValues(
    declarations: CSSDeclaration[],
    propertyPatterns: string[]
): Map<string, string[]> {
    const result = new Map<string, string[]>();

    for (const pattern of propertyPatterns) {
        const values: string[] = [];

        for (const decl of declarations) {
            if (decl.property.includes(pattern) || decl.property === pattern) {
                if (!values.includes(decl.value)) {
                    values.push(decl.value);
                }
            }
        }

        if (values.length > 0) {
            result.set(pattern, values);
        }
    }

    return result;
}
