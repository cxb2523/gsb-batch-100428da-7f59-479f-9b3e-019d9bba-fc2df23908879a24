import type { Post } from '@telepat/otto-protocol';
import type { SiteCommand } from '../types.js';

const DEFAULT_MIN_RETURNED_POSTS = 20;
const MAX_MIN_RETURNED_POSTS = 100;
const MAX_CONCURRENT_FETCHES = 4;
const FETCH_TIMEOUT_MS = 8_000;
const RETRY_BACKOFF_MS = 2_000;
const MAX_RETRY_BACKOFF_MS = 8_000;
const INTER_REQUEST_DELAY_MS = 500;
const MAX_DETAIL_RETRIES = 3;
const MAX_LISTING_RETRIES = 2;

const VALID_SOURCES = ['home', 'subreddit', 'user'];
const VALID_SORTS = ['best', 'hot', 'new', 'top', 'rising'];
const VALID_T = ['hour', 'day', 'week', 'month', 'year', 'all'];

export type PostFetchFailureReason =
  | 'rate_limited'
  | 'timeout'
  | 'http_error'
  | 'parse_error'
  | 'network_error';

export type PostFetchFailure = {
  url: string;
  reason: PostFetchFailureReason;
  status?: number;
  message: string;
};

export type PostListingSummary = {
  targetPosts: number;
  collectedPosts: number;
  complete: boolean;
  failure?: Omit<PostFetchFailure, 'url'>;
};

export type PostCollectionSummary = {
  requestedPosts: number;
  successfulPosts: number;
  failedPosts: number;
  failures: PostFetchFailure[];
  listing: PostListingSummary;
};

export type RedditPostCollectionResult = {
  posts: Record<string, unknown>[];
  summary: PostCollectionSummary;
};

export type RedditPostCollectionConfig = {
  requestLimit: number;
  maxConcurrentFetches: number;
  fetchTimeoutMs: number;
  retryBackoffMs: number;
  maxRetryBackoffMs: number;
  interRequestDelayMs: number;
  maxDetailRetries: number;
  maxListingRetries: number;
  source: string;
  subreddit: string | null;
  username: string | null;
  sort: string;
  t: string;
};

const POST_FETCH_FAILURE_REASONS = new Set<PostFetchFailureReason>([
  'rate_limited',
  'timeout',
  'http_error',
  'parse_error',
  'network_error',
]);

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null;

const readCount = (value: unknown, fallback: number): number =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0
    ? Math.floor(value)
    : fallback;

const readFailureDetail = (value: unknown): Omit<PostFetchFailure, 'url'> | null => {
  if (!isRecord(value)) {
    return null;
  }

  const reason = typeof value.reason === 'string' ? value.reason : '';
  if (!POST_FETCH_FAILURE_REASONS.has(reason as PostFetchFailureReason)) {
    return null;
  }

  const status = typeof value.status === 'number' && Number.isFinite(value.status)
    ? value.status
    : undefined;

  return {
    reason: reason as PostFetchFailureReason,
    ...(status !== undefined ? { status } : {}),
    message: typeof value.message === 'string' ? value.message : reason,
  };
};

const readFailure = (value: unknown): PostFetchFailure | null => {
  if (!isRecord(value)) {
    return null;
  }

  const url = typeof value.url === 'string' ? value.url : '';
  if (!url) {
    return null;
  }

  const detail = readFailureDetail(value);
  if (!detail) {
    return null;
  }

  return { url, ...detail };
};

const normalizeCollectionSummary = (
  value: unknown,
  targetPosts: number,
  posts: Post[],
): PostCollectionSummary => {
  if (!isRecord(value)) {
    return {
      requestedPosts: posts.length,
      successfulPosts: posts.length,
      failedPosts: 0,
      failures: [],
      listing: {
        targetPosts,
        collectedPosts: posts.length,
        complete: posts.length >= targetPosts,
      },
    };
  }

  const rawFailures = Array.isArray(value.failures) ? value.failures : [];
  const failures = rawFailures
    .map(readFailure)
    .filter((failure): failure is PostFetchFailure => failure !== null);

  const requestedPosts = readCount(value.requestedPosts, posts.length + failures.length);
  const rawListingValue = value.listing;
  const rawListing = isRecord(rawListingValue) ? rawListingValue : null;
  const listingFailure = rawListing ? readFailureDetail(rawListing.failure) : null;
  const rawListingComplete = rawListing ? rawListing.complete : undefined;

  return {
    requestedPosts,
    successfulPosts: posts.length,
    failedPosts: failures.length,
    failures,
    listing: {
      targetPosts: readCount(rawListing ? rawListing.targetPosts : undefined, targetPosts),
      collectedPosts: readCount(rawListing ? rawListing.collectedPosts : undefined, requestedPosts),
      complete: typeof rawListingComplete === 'boolean'
        ? rawListingComplete
        : posts.length + failures.length >= targetPosts,
      ...(listingFailure ? { failure: listingFailure } : {}),
    },
  };
};

/**
 * Self-contained collector injected into the Reddit page via `chrome.scripting.executeScript`.
 * Keep this function free of outer-scope references: Chrome serializes it for in-page execution.
 * It is exported so tests can drive it with a controlled `fetch` stub.
 */
export async function collectRedditPosts(config: RedditPostCollectionConfig): Promise<RedditPostCollectionResult> {
  type GenericRecord = Record<string, unknown>;
  type RedditListingNode = { kind?: unknown; data?: GenericRecord };
  type FetchFailureReason = PostFetchFailureReason;
  type FetchFailure = { reason: FetchFailureReason; status?: number; message: string; retryAfterMs?: number };
  type FetchOutcome = { ok: true; payload: unknown } | { ok: false; failure: FetchFailure };
  type DetailResult = { post: GenericRecord | null; failure: (FetchFailure & { url: string }) | null };

  const {
    requestLimit,
    maxConcurrentFetches,
    fetchTimeoutMs,
    retryBackoffMs,
    maxRetryBackoffMs,
    interRequestDelayMs,
    maxDetailRetries,
    maxListingRetries,
    source,
    subreddit,
    username,
    sort,
    t,
  } = config;

  const RETRY_AFTER_PADDING_MS = 1000;

  const sleep = (ms: number): Promise<void> => new Promise((resolve) => {
    setTimeout(resolve, ms);
  });

  const parseRateLimitReset = (headers: Headers): number | null => {
    const resetStr = headers.get('x-ratelimit-reset');
    if (resetStr !== null) {
      const reset = Number(resetStr);
      if (Number.isFinite(reset) && reset > 0) {
        return reset * 1000;
      }
    }
    return null;
  };

  const toIsoFromSeconds = (value: unknown): string | undefined => {
    if (typeof value !== 'number' || !Number.isFinite(value)) {
      return undefined;
    }
    return new Date(value * 1000).toISOString();
  };

  const toIsoFromEdited = (value: unknown): string | undefined => {
    if (typeof value === 'number' && Number.isFinite(value)) {
      return new Date(value * 1000).toISOString();
    }
    if (typeof value === 'string' && value.trim().length > 0) {
      return value;
    }
    return undefined;
  };

  const createUserRef = (author: unknown, fullName: unknown): GenericRecord | undefined => {
    const authorText = typeof author === 'string' ? author.trim() : '';
    const fullNameText = typeof fullName === 'string' ? fullName.trim() : '';
    if (!authorText && !fullNameText) {
      return undefined;
    }

    return {
      id: fullNameText || authorText || 'unknown',
      username: authorText || undefined,
      displayName: authorText || undefined,
      platform: 'reddit',
    };
  };

  const resolveBaseOrigin = (): string => {
    if (typeof window !== 'undefined' && window.location && window.location.origin) {
      return window.location.origin;
    }
    return 'https://www.reddit.com';
  };

  const normalizeRedditUrl = (value: string): string | null => {
    if (!value || value.trim().length === 0) {
      return null;
    }

    try {
      const parsed = new URL(value, resolveBaseOrigin());
      const host = parsed.hostname.toLowerCase();
      if (!host.endsWith('reddit.com')) {
        return null;
      }

      parsed.protocol = 'https:';
      parsed.hostname = 'www.reddit.com';
      parsed.hash = '';
      return parsed.toString();
    } catch {
      return null;
    }
  };

  const appendJsonSuffix = (value: string, limit: number): string => {
    const parsed = new URL(value);
    if (!parsed.pathname.endsWith('.json')) {
      parsed.pathname = parsed.pathname.endsWith('/')
        ? `${parsed.pathname.slice(0, -1)}.json`
        : `${parsed.pathname}.json`;
    }
    parsed.searchParams.set('limit', String(limit));
    parsed.searchParams.set('raw_json', '1');
    return parsed.toString();
  };

  const toCanonicalPostUrl = (permalink: unknown, fallback: string): string => {
    if (typeof permalink === 'string' && permalink.trim().length > 0) {
      const normalized = normalizeRedditUrl(permalink);
      if (normalized) {
        return normalized;
      }
    }
    return fallback;
  };

  const mapCommentNode = (
    node: unknown,
    parentPostId: string,
    parentCommentId: string | undefined,
    depth: number,
  ): GenericRecord | null => {
    if (!node || typeof node !== 'object') {
      return null;
    }

    const typedNode = node as RedditListingNode;
    if (typedNode.kind !== 't1' || !typedNode.data) {
      return null;
    }

    const data = typedNode.data;
    const idValue = typeof data.id === 'string' ? data.id : (typeof data.name === 'string' ? data.name : '');
    if (!idValue) {
      return null;
    }

    const replies: GenericRecord[] = [];
    const rawReplies = data.replies;
    if (rawReplies && typeof rawReplies === 'object') {
      const replyChildren = (rawReplies as { data?: { children?: unknown } }).data?.children;
      if (Array.isArray(replyChildren)) {
        for (const child of replyChildren) {
          const mapped = mapCommentNode(child, parentPostId, idValue, depth + 1);
          if (mapped) {
            replies.push(mapped);
          }
        }
      }
    }

    return {
      kind: 'content.post_comment',
      id: idValue,
      parentPostId,
      parentCommentId,
      author: createUserRef(data.author, data.author_fullname),
      content: typeof data.body === 'string' ? data.body : undefined,
      publishedAt: toIsoFromSeconds(data.created_utc),
      editedAt: toIsoFromEdited(data.edited),
      score: typeof data.score === 'number' ? data.score : undefined,
      depth,
      comments: replies,
      meta: {
        site: 'reddit.com',
        source: 'reddit.json',
        rawEventType: 't1',
      },
      originalEntity: data,
    };
  };

  const mapPostPayload = (payload: unknown, fallbackUrl: string): GenericRecord | null => {
    if (!Array.isArray(payload) || payload.length === 0) {
      return null;
    }

    const listing = payload[0] as { data?: { children?: unknown } } | undefined;
    const children = listing?.data?.children;
    if (!Array.isArray(children)) {
      return null;
    }

    const postNode = children.find((entry) => {
      if (!entry || typeof entry !== 'object') {
        return false;
      }
      return (entry as RedditListingNode).kind === 't3';
    }) as RedditListingNode | undefined;

    if (!postNode?.data) {
      return null;
    }

    const postData = postNode.data;
    const postId = typeof postData.id === 'string' ? postData.id : (typeof postData.name === 'string' ? postData.name : '');
    if (!postId) {
      return null;
    }

    const commentNodes = (payload[1] as { data?: { children?: unknown } } | undefined)?.data?.children;
    const comments: GenericRecord[] = [];
    if (Array.isArray(commentNodes)) {
      for (const commentNode of commentNodes) {
        const mapped = mapCommentNode(commentNode, postId, undefined, 0);
        if (mapped) {
          comments.push(mapped);
        }
      }
    }

    const canonicalUrl = toCanonicalPostUrl(postData.permalink, fallbackUrl);
    const title = typeof postData.title === 'string' ? postData.title : '';
    if (!title) {
      return null;
    }

    return {
      kind: 'content.post',
      id: postId,
      title,
      url: canonicalUrl,
      author: createUserRef(postData.author, postData.author_fullname),
      publishedAt: toIsoFromSeconds(postData.created_utc),
      editedAt: toIsoFromEdited(postData.edited),
      content: typeof postData.selftext === 'string' && postData.selftext.trim().length > 0
        ? postData.selftext
        : undefined,
      community: typeof postData.subreddit_name_prefixed === 'string' && postData.subreddit_name_prefixed.trim().length > 0
        ? postData.subreddit_name_prefixed
        : (typeof postData.subreddit === 'string' && postData.subreddit.trim().length > 0
          ? `r/${postData.subreddit}`
          : undefined),
      score: typeof postData.score === 'number' ? postData.score : undefined,
      commentCount: typeof postData.num_comments === 'number' ? postData.num_comments : undefined,
      comments,
      meta: {
        site: 'reddit.com',
        source: 'reddit.json',
        rawEventType: 't3',
      },
      originalEntity: postData,
    };
  };

  const buildFeedJsonUrl = (feedSource: string, feedSubreddit: string | null, feedUsername: string | null, feedSort: string, feedT: string, limit: number): string => {
    let pathname: string;
    if (feedSource === 'user') {
      pathname = `/user/${encodeURIComponent(feedUsername ?? '')}/submitted`;
    } else if (feedSource === 'subreddit') {
      pathname = `/r/${encodeURIComponent(feedSubreddit ?? '')}/${feedSort}`;
    } else {
      pathname = `/${feedSort}`;
    }

    pathname = pathname.replace(/\/+$/, '');
    const params = new URLSearchParams();
    params.set('limit', String(limit));
    params.set('raw_json', '1');
    if (feedSort === 'top' && feedT) {
      params.set('t', feedT);
    }

    return `https://www.reddit.com${pathname}.json?${params.toString()}`;
  };

  const fetchJsonOnce = async (url: string, timeoutMs: number): Promise<FetchOutcome> => {
    const controller = new AbortController();
    const timer = setTimeout(() => {
      controller.abort();
    }, timeoutMs);

    try {
      const response = await fetch(url, {
        method: 'GET',
        credentials: 'include',
        cache: 'no-store',
        headers: {
          Accept: 'application/json',
        },
        signal: controller.signal,
      });

      if (response.status === 429) {
        const retryAfterMs = parseRateLimitReset(response.headers);
        return {
          ok: false,
          failure: {
            reason: 'rate_limited',
            status: 429,
            message: 'HTTP 429 rate limited',
            ...(retryAfterMs !== null ? { retryAfterMs } : {}),
          },
        };
      }

      if (!response.ok) {
        return {
          ok: false,
          failure: {
            reason: 'http_error',
            status: response.status,
            message: `HTTP ${response.status}`,
          },
        };
      }

      try {
        return { ok: true, payload: await response.json() };
      } catch {
        if (controller.signal.aborted) {
          return {
            ok: false,
            failure: {
              reason: 'timeout',
              message: `request timed out after ${timeoutMs}ms`,
            },
          };
        }

        return {
          ok: false,
          failure: {
            reason: 'parse_error',
            message: 'response body is not valid JSON',
          },
        };
      }
    } catch {
      if (controller.signal.aborted) {
        return {
          ok: false,
          failure: {
            reason: 'timeout',
            message: `request timed out after ${timeoutMs}ms`,
          },
        };
      }

      return {
        ok: false,
        failure: {
          reason: 'network_error',
          message: 'network request failed',
        },
      };
    } finally {
      clearTimeout(timer);
    }
  };

  const isRetryableFailure = (failure: FetchFailure): boolean => {
    if (failure.reason === 'http_error') {
      return typeof failure.status === 'number' && failure.status >= 500;
    }
    return true;
  };

  const requestJson = async (url: string, timeoutMs: number, maxRetries: number): Promise<FetchOutcome> => {
    let lastFailure: FetchFailure = {
      reason: 'network_error',
      message: 'request failed',
    };

    for (let attempt = 0; attempt <= maxRetries; attempt++) {
      const outcome = await fetchJsonOnce(url, timeoutMs);
      if (outcome.ok) {
        return outcome;
      }

      lastFailure = outcome.failure;
      if (attempt >= maxRetries || !isRetryableFailure(outcome.failure)) {
        break;
      }

      const retryAfterMs = outcome.failure.retryAfterMs;
      const backoffMs = typeof retryAfterMs === 'number'
        ? retryAfterMs + RETRY_AFTER_PADDING_MS
        : retryBackoffMs * Math.pow(2, attempt);
      await sleep(Math.max(0, Math.min(backoffMs, maxRetryBackoffMs)));
    }

    return { ok: false, failure: lastFailure };
  };

  const collectPostUrlsFromJsonFeed = async (
    feedJsonUrl: string,
    minCount: number,
    timeoutMs: number,
  ): Promise<{ postUrls: string[]; failure: FetchFailure | null; complete: boolean }> => {
    const collected: string[] = [];
    let after: string | null = null;
    let failure: FetchFailure | null = null;

    while (collected.length < minCount) {
      const pageUrl = after
        ? `${feedJsonUrl}&after=${encodeURIComponent(after)}`
        : feedJsonUrl;

      const outcome = await requestJson(pageUrl, timeoutMs, maxListingRetries);
      if (!outcome.ok) {
        failure = outcome.failure;
        break;
      }

      const payload = outcome.payload;
      if (!payload || typeof payload !== 'object') {
        failure = {
          reason: 'parse_error',
          message: 'listing payload is not a JSON object',
        };
        break;
      }

      const listing = (payload as { data?: { children?: unknown[]; after?: string | null } }).data;
      if (!listing || typeof listing !== 'object') {
        failure = {
          reason: 'parse_error',
          message: 'listing payload is missing data',
        };
        break;
      }

      const children = Array.isArray(listing.children) ? listing.children : [];
      after = typeof listing.after === 'string' && listing.after.length > 0 ? listing.after : null;

      for (const child of children) {
        if (!child || typeof child !== 'object') {
          continue;
        }

        const node = child as { kind?: string; data?: { permalink?: string } };
        if (node.kind !== 't3' || !node.data?.permalink) {
          continue;
        }

        const normalized = normalizeRedditUrl(node.data.permalink);
        if (normalized && normalized.includes('/comments/')) {
          collected.push(normalized);
        }
      }

      if (!after || children.length === 0) {
        break;
      }
    }

    return { postUrls: collected, failure, complete: collected.length >= minCount };
  };

  const toFailureDetail = (failure: FetchFailure): Omit<PostFetchFailure, 'url'> => ({
    reason: failure.reason,
    ...(typeof failure.status === 'number' ? { status: failure.status } : {}),
    message: failure.message,
  });

  const feedJsonUrl = buildFeedJsonUrl(source, subreddit, username, sort, t, requestLimit);
  const listingResult = await collectPostUrlsFromJsonFeed(
    feedJsonUrl,
    requestLimit,
    fetchTimeoutMs,
  );

  const listingSummary: PostListingSummary = {
    targetPosts: requestLimit,
    collectedPosts: listingResult.postUrls.length,
    complete: listingResult.complete,
    ...(listingResult.failure ? { failure: toFailureDetail(listingResult.failure) } : {}),
  };

  const postUrls = listingResult.postUrls;
  if (postUrls.length === 0) {
    return {
      posts: [],
      summary: {
        requestedPosts: 0,
        successfulPosts: 0,
        failedPosts: 0,
        failures: [],
        listing: listingSummary,
      },
    };
  }

  const results: DetailResult[] = new Array(postUrls.length);
  const workerCount = Math.max(1, Math.min(maxConcurrentFetches, postUrls.length));
  let cursor = 0;

  const runWorker = async (): Promise<void> => {
    while (true) {
      const index = cursor;
      cursor += 1;
      if (index >= postUrls.length) {
        return;
      }

      const postUrl = postUrls[index];
      const jsonUrl = appendJsonSuffix(postUrl, requestLimit);
      const outcome = await requestJson(jsonUrl, fetchTimeoutMs, maxDetailRetries);

      if (outcome.ok) {
        try {
          const mapped = mapPostPayload(outcome.payload, postUrl);
          results[index] = mapped
            ? { post: mapped, failure: null }
            : {
              post: null,
              failure: {
                url: postUrl,
                reason: 'parse_error',
                message: 'post payload is missing required fields',
              },
            };
        } catch {
          results[index] = {
            post: null,
            failure: {
              url: postUrl,
              reason: 'parse_error',
              message: 'rendering post payload failed',
            },
          };
        }
      } else {
        results[index] = {
          post: null,
          failure: { url: postUrl, ...outcome.failure },
        };
      }

      if (index + 1 < postUrls.length) {
        await sleep(interRequestDelayMs);
      }
    }
  };

  await Promise.all(Array.from({ length: workerCount }, async () => {
    await runWorker();
  }));

  const outputPosts: GenericRecord[] = [];
  const failures: PostFetchFailure[] = [];
  for (const result of results) {
    if (!result) {
      continue;
    }
    if (result.post) {
      outputPosts.push(result.post);
    } else if (result.failure) {
      failures.push({
        url: result.failure.url,
        ...toFailureDetail(result.failure),
      });
    }
  }

  return {
    posts: outputPosts,
    summary: {
      requestedPosts: postUrls.length,
      successfulPosts: outputPosts.length,
      failedPosts: failures.length,
      failures,
      listing: listingSummary,
    },
  };
}

export const getPostsCommand: SiteCommand = {
  metadata: {
    site: 'reddit.com',
    id: 'getPosts',
    displayName: 'Get Reddit Posts',
    description: 'Fetches Reddit posts via the JSON API from home feed, subreddit listings, or user submitted posts with configurable sort and time filters.',
    tags: ['feed', 'posts', 'reddit'],
    requiresAuth: true,
    requiresDebuggerFocus: true,
    preloadHost: 'www.reddit.com',
    timeoutPolicy: {
      defaultMs: 60_000,
      scaling: {
        inputField: 'minReturnedPosts',
        baseMs: 45_000,
        perUnitMs: 2_000,
        minMs: 45_000,
        maxMs: 300_000,
      },
    },
    inputFields: [
      {
        name: 'source',
        type: 'string',
        description: 'Feed source: home (default), subreddit, or user.',
        optional: true,
      },
      {
        name: 'subreddit',
        type: 'string',
        description: 'Subreddit name without r/ prefix. Required when source is subreddit.',
        optional: true,
      },
      {
        name: 'username',
        type: 'string',
        description: 'Reddit username without u/ prefix. Required when source is user.',
        optional: true,
      },
      {
        name: 'sort',
        type: 'string',
        description: 'Sort order for home and subreddit sources: best (default), hot, new, top, rising.',
        optional: true,
      },
      {
        name: 't',
        type: 'string',
        description: 'Time filter when sort is top: hour, day (default), week, month, year, all.',
        optional: true,
      },
      {
        name: 'minReturnedPosts',
        type: 'number',
        description: 'Minimum number of posts to return (default 20, max 100). Also used as the API page limit.',
        optional: true,
      },
    ],
  },
  async execute(ctx, input) {
    const source = typeof input.source === 'string' ? input.source.trim() : 'home';
    if (!VALID_SOURCES.includes(source)) {
      throw new Error(`getPosts source must be one of: ${VALID_SOURCES.join(', ')}`);
    }

    const subredditRaw = typeof input.subreddit === 'string' ? input.subreddit.trim() : '';
    const subreddit = subredditRaw.replace(/^r\//, '') || null;
    if (source === 'subreddit' && !subreddit) {
      throw new Error('getPosts requires subreddit when source is subreddit');
    }

    const usernameRaw = typeof input.username === 'string' ? input.username.trim() : '';
    const username = usernameRaw.replace(/^u\//, '') || null;
    if (source === 'user' && !username) {
      throw new Error('getPosts requires username when source is user');
    }

    const sort = typeof input.sort === 'string' ? input.sort.trim() : 'best';
    if (!VALID_SORTS.includes(sort)) {
      throw new Error(`getPosts sort must be one of: ${VALID_SORTS.join(', ')}`);
    }

    const t = typeof input.t === 'string' ? input.t.trim() : 'day';
    if (!VALID_T.includes(t)) {
      throw new Error(`getPosts t must be one of: ${VALID_T.join(', ')}`);
    }

    const minReturnedPostsRaw = input.minReturnedPosts;
    const minReturnedPosts = typeof minReturnedPostsRaw === 'number' && Number.isFinite(minReturnedPostsRaw)
      ? minReturnedPostsRaw
      : DEFAULT_MIN_RETURNED_POSTS;
    const boundedMinReturnedPosts = Math.max(1, Math.min(MAX_MIN_RETURNED_POSTS, Math.floor(minReturnedPosts)));

    const result = await ctx.executeScript(
      collectRedditPosts,
      [{
        requestLimit: boundedMinReturnedPosts,
        maxConcurrentFetches: MAX_CONCURRENT_FETCHES,
        fetchTimeoutMs: FETCH_TIMEOUT_MS,
        retryBackoffMs: RETRY_BACKOFF_MS,
        maxRetryBackoffMs: MAX_RETRY_BACKOFF_MS,
        interRequestDelayMs: INTER_REQUEST_DELAY_MS,
        maxDetailRetries: MAX_DETAIL_RETRIES,
        maxListingRetries: MAX_LISTING_RETRIES,
        source,
        subreddit,
        username,
        sort,
        t,
      }],
    );

    const rawResult: Record<string, unknown> = isRecord(result) ? result : {};
    const rawPosts: unknown[] = Array.isArray(rawResult.posts) ? rawResult.posts : [];

    const posts = rawPosts.filter((value): value is Post => {
      if (!value || typeof value !== 'object') {
        return false;
      }

      const post = value as { kind?: unknown; id?: unknown; title?: unknown };
      return post.kind === 'content.post'
        && typeof post.id === 'string'
        && typeof post.title === 'string';
    });

    const summary = normalizeCollectionSummary(rawResult.summary, boundedMinReturnedPosts, posts);

    return { posts, summary };
  },
};
