import assert from 'node:assert/strict';
import test from 'node:test';
import {
  collectRedditPosts,
  getPostsCommand,
  type RedditPostCollectionConfig,
} from '../src/commands/reddit.com/get-posts.js';
import type { CommandExecutionContext } from '../src/commands/types.js';

type StubResponse = {
  status: number;
  ok: boolean;
  headers: { get: (name: string) => string | null };
  json: () => Promise<unknown>;
};

type StubHandler = (url: string, callIndex: number, init: RequestInit | undefined) => StubResponse | Promise<StubResponse>;

type FetchStub = {
  calls: string[];
  countFor: (predicate: (url: string) => boolean) => number;
  stub: typeof fetch;
};

const createFetchStub = (handler: StubHandler): FetchStub => {
  const calls: string[] = [];
  const stub = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString();
    const callIndex = calls.filter((entry) => entry === url).length;
    calls.push(url);
    return await handler(url, callIndex, init);
  }) as typeof fetch;

  return {
    calls,
    countFor: (predicate) => calls.filter(predicate).length,
    stub,
  };
};

const jsonResponse = (body: unknown, status = 200): StubResponse => ({
  status,
  ok: status >= 200 && status < 300,
  headers: { get: () => null },
  json: async () => body,
});

const rateLimitResponse = (): StubResponse => ({
  status: 429,
  ok: false,
  headers: { get: () => null },
  json: async () => ({ message: 'Too Many Requests' }),
});

const abortingResponse = (init: RequestInit): Promise<StubResponse> => new Promise((_resolve, reject) => {
  const signal = init.signal;
  if (!signal) {
    reject(new Error('missing abort signal'));
    return;
  }

  if (signal.aborted) {
    reject(new DOMException('The operation was aborted.', 'AbortError'));
    return;
  }

  signal.addEventListener('abort', () => {
    reject(new DOMException('The operation was aborted.', 'AbortError'));
  });
});

const buildListingPage = (permalinks: string[], after: string | null) => ({
  kind: 'Listing',
  data: {
    after,
    children: permalinks.map((permalink) => ({
      kind: 't3',
      data: { permalink },
    })),
  },
});

const buildPostPayload = (postId: string, title: string, permalink: string) => [
  {
    kind: 'Listing',
    data: {
      children: [
        {
          kind: 't3',
          data: {
            id: postId,
            name: `t3_${postId}`,
            title,
            permalink,
            author: 'alice',
            subreddit: 'test',
            subreddit_name_prefixed: 'r/test',
            created_utc: 1_700_000_000,
            score: 42,
            num_comments: 3,
            selftext: 'body',
          },
        },
      ],
    },
  },
  {
    kind: 'Listing',
    data: { children: [] },
  },
];

const PERMALINKS = {
  aaa: '/r/test/comments/aaa/post_aaa/',
  bbb: '/r/test/comments/bbb/post_bbb/',
  ccc: '/r/test/comments/ccc/post_ccc/',
};

const CANONICAL_URLS = {
  aaa: 'https://www.reddit.com/r/test/comments/aaa/post_aaa/',
  bbb: 'https://www.reddit.com/r/test/comments/bbb/post_bbb/',
  ccc: 'https://www.reddit.com/r/test/comments/ccc/post_ccc/',
};

const makeConfig = (overrides: Partial<RedditPostCollectionConfig> = {}): RedditPostCollectionConfig => ({
  requestLimit: 5,
  maxConcurrentFetches: 2,
  fetchTimeoutMs: 20,
  retryBackoffMs: 0,
  maxRetryBackoffMs: 0,
  interRequestDelayMs: 0,
  maxDetailRetries: 3,
  maxListingRetries: 2,
  source: 'home',
  subreddit: null,
  username: null,
  sort: 'best',
  t: 'day',
  ...overrides,
});

const isDetailUrl = (url: string, key: keyof typeof PERMALINKS): boolean =>
  url.includes(`/comments/${key}/`);

test('getPostCollection keeps listing posts when a later listing page fails', async () => {
  const fetchStub = createFetchStub((url) => {
    if (url.includes('/best.json')) {
      if (url.includes('after=')) {
        return rateLimitResponse();
      }
      return jsonResponse(buildListingPage([PERMALINKS.aaa, PERMALINKS.bbb], 't3_cursor'));
    }

    if (isDetailUrl(url, 'aaa')) {
      return jsonResponse(buildPostPayload('aaa', 'Post AAA', PERMALINKS.aaa));
    }
    if (isDetailUrl(url, 'bbb')) {
      return jsonResponse(buildPostPayload('bbb', 'Post BBB', PERMALINKS.bbb));
    }
    throw new Error(`unexpected url: ${url}`);
  });

  const originalFetch = globalThis.fetch;
  globalThis.fetch = fetchStub.stub;
  try {
    const result = await collectRedditPosts(makeConfig());

    assert.deepEqual(result.posts.map((post) => post.id), ['aaa', 'bbb']);
    assert.equal(result.posts.length, 2);
    assert.deepEqual(result.summary, {
      requestedPosts: 2,
      successfulPosts: 2,
      failedPosts: 0,
      failures: [],
      listing: {
        targetPosts: 5,
        collectedPosts: 2,
        complete: false,
        failure: {
          reason: 'rate_limited',
          status: 429,
          message: 'HTTP 429 rate limited',
        },
      },
    });
    assert.equal(fetchStub.countFor((url) => url.includes('after=')), 3);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('getPostCollection records a post that keeps returning 429 after retries', async () => {
  const fetchStub = createFetchStub((url) => {
    if (url.includes('/best.json')) {
      return jsonResponse(buildListingPage([PERMALINKS.aaa, PERMALINKS.bbb, PERMALINKS.ccc], null));
    }

    if (isDetailUrl(url, 'bbb')) {
      return rateLimitResponse();
    }
    if (isDetailUrl(url, 'aaa')) {
      return jsonResponse(buildPostPayload('aaa', 'Post AAA', PERMALINKS.aaa));
    }
    if (isDetailUrl(url, 'ccc')) {
      return jsonResponse(buildPostPayload('ccc', 'Post CCC', PERMALINKS.ccc));
    }
    throw new Error(`unexpected url: ${url}`);
  });

  const originalFetch = globalThis.fetch;
  globalThis.fetch = fetchStub.stub;
  try {
    const result = await collectRedditPosts(makeConfig());

    assert.deepEqual(result.posts.map((post) => post.id), ['aaa', 'ccc']);
    assert.equal(result.summary.requestedPosts, 3);
    assert.equal(result.summary.successfulPosts, 2);
    assert.equal(result.summary.failedPosts, 1);
    assert.deepEqual(result.summary.failures, [
      {
        url: CANONICAL_URLS.bbb,
        reason: 'rate_limited',
        status: 429,
        message: 'HTTP 429 rate limited',
      },
    ]);
    assert.equal(result.summary.listing.complete, false);
    assert.equal(result.summary.listing.failure, undefined);
    assert.equal(fetchStub.countFor((url) => isDetailUrl(url, 'bbb')), 4);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('getPostCollection records a post detail request that times out', async () => {
  const fetchStub = createFetchStub((url, _callIndex, init) => {
    if (url.includes('/best.json')) {
      return jsonResponse(buildListingPage([PERMALINKS.aaa, PERMALINKS.bbb], null));
    }
    if (isDetailUrl(url, 'aaa')) {
      return jsonResponse(buildPostPayload('aaa', 'Post AAA', PERMALINKS.aaa));
    }
    if (isDetailUrl(url, 'bbb')) {
      // Simulate a hanging request that only settles when the AbortController fires.
      return abortingResponse(init ?? {});
    }
    throw new Error(`unexpected url: ${url}`);
  });

  const originalFetch = globalThis.fetch;
  globalThis.fetch = fetchStub.stub;
  try {
    const result = await collectRedditPosts(makeConfig());

    assert.deepEqual(result.posts.map((post) => post.id), ['aaa']);
    assert.equal(result.summary.requestedPosts, 2);
    assert.equal(result.summary.successfulPosts, 1);
    assert.equal(result.summary.failedPosts, 1);
    assert.equal(result.summary.failures.length, 1);
    const failure = result.summary.failures[0];
    assert.ok(failure);
    assert.equal(failure.url, CANONICAL_URLS.bbb);
    assert.equal(failure.reason, 'timeout');
    assert.match(failure.message, /timed out/);
    assert.equal(failure.status, undefined);
    assert.equal(fetchStub.countFor((url) => isDetailUrl(url, 'bbb')), 4);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('getPostCollection records parse failures for unusable post payloads', async () => {
  const fetchStub = createFetchStub((url) => {
    if (url.includes('/best.json')) {
      return jsonResponse(buildListingPage([PERMALINKS.aaa, PERMALINKS.bbb], null));
    }
    if (isDetailUrl(url, 'aaa')) {
      return jsonResponse(buildPostPayload('aaa', 'Post AAA', PERMALINKS.aaa));
    }
    if (isDetailUrl(url, 'bbb')) {
      // Valid JSON, but no t3 node with a title.
      return jsonResponse([{ kind: 'Listing', data: { children: [] } }]);
    }
    throw new Error(`unexpected url: ${url}`);
  });

  const originalFetch = globalThis.fetch;
  globalThis.fetch = fetchStub.stub;
  try {
    const result = await collectRedditPosts(makeConfig());

    assert.deepEqual(result.posts.map((post) => post.id), ['aaa']);
    assert.equal(result.summary.failedPosts, 1);
    assert.deepEqual(result.summary.failures, [
      {
        url: CANONICAL_URLS.bbb,
        reason: 'parse_error',
        message: 'post payload is missing required fields',
      },
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('getPostCollection does not retry non-retryable HTTP errors', async () => {
  const fetchStub = createFetchStub((url) => {
    if (url.includes('/best.json')) {
      return jsonResponse(buildListingPage([PERMALINKS.aaa, PERMALINKS.bbb], null));
    }
    if (isDetailUrl(url, 'aaa')) {
      return jsonResponse(buildPostPayload('aaa', 'Post AAA', PERMALINKS.aaa));
    }
    if (isDetailUrl(url, 'bbb')) {
      return jsonResponse({ message: 'Forbidden' }, 403);
    }
    throw new Error(`unexpected url: ${url}`);
  });

  const originalFetch = globalThis.fetch;
  globalThis.fetch = fetchStub.stub;
  try {
    const result = await collectRedditPosts(makeConfig());

    assert.deepEqual(result.posts.map((post) => post.id), ['aaa']);
    assert.deepEqual(result.summary.failures, [
      {
        url: CANONICAL_URLS.bbb,
        reason: 'http_error',
        status: 403,
        message: 'HTTP 403',
      },
    ]);
    assert.equal(fetchStub.countFor((url) => isDetailUrl(url, 'bbb')), 1);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('getPostCollection returns every post with an empty failure list on success', async () => {
  const fetchStub = createFetchStub((url) => {
    if (url.includes('/best.json')) {
      return jsonResponse(buildListingPage([PERMALINKS.aaa, PERMALINKS.bbb], null));
    }
    if (isDetailUrl(url, 'aaa')) {
      return jsonResponse(buildPostPayload('aaa', 'Post AAA', PERMALINKS.aaa));
    }
    if (isDetailUrl(url, 'bbb')) {
      return jsonResponse(buildPostPayload('bbb', 'Post BBB', PERMALINKS.bbb));
    }
    throw new Error(`unexpected url: ${url}`);
  });

  const originalFetch = globalThis.fetch;
  globalThis.fetch = fetchStub.stub;
  try {
    const result = await collectRedditPosts(makeConfig({ requestLimit: 2 }));

    assert.deepEqual(result.posts.map((post) => post.id), ['aaa', 'bbb']);
    assert.equal(result.posts[0]?.url, CANONICAL_URLS.aaa);
    assert.equal(result.posts[0]?.title, 'Post AAA');
    assert.deepEqual(result.summary, {
      requestedPosts: 2,
      successfulPosts: 2,
      failedPosts: 0,
      failures: [],
      listing: {
        targetPosts: 2,
        collectedPosts: 2,
        complete: true,
      },
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

const buildStubContext = (): CommandExecutionContext => ({
  chromeApi: {} as CommandExecutionContext['chromeApi'],
  tabId: 11,
  tabSessionId: 'tab_alpha',
  debug: {
    enabled: false,
    log: async () => {},
  },
  isSerializedScriptError: (value: unknown): value is never => false,
  getTabUrl: async () => 'https://www.reddit.com/',
  navigateTab: async () => {},
  executeScript: async (func, args) => await func(...args),
  executeScriptWithDomHelpers: async (func, args) => await func(...args),
  startNetworkInterception: async () => {
    throw new Error('not implemented in test');
  },
});

const execGetPosts = (input: Record<string, unknown>) =>
  getPostsCommand.execute(buildStubContext(), input, 'auto');

test('getPosts command returns posts plus a collection summary', async () => {
  const fetchStub = createFetchStub((url) => {
    if (url.includes('/best.json')) {
      return jsonResponse(buildListingPage([PERMALINKS.aaa, PERMALINKS.bbb], null));
    }
    if (isDetailUrl(url, 'aaa')) {
      return jsonResponse(buildPostPayload('aaa', 'Post AAA', PERMALINKS.aaa));
    }
    if (isDetailUrl(url, 'bbb')) {
      // Non-retryable status keeps the command-level test fast while still exercising the summary path.
      return jsonResponse({ message: 'Gone' }, 404);
    }
    throw new Error(`unexpected url: ${url}`);
  });

  const originalFetch = globalThis.fetch;
  globalThis.fetch = fetchStub.stub;
  try {
    const output = await execGetPosts({ minReturnedPosts: 2 }) as {
      posts: Array<Record<string, unknown>>;
      summary: Record<string, unknown>;
    };

    assert.deepEqual(output.posts.map((post) => post.id), ['aaa']);
    assert.equal(output.summary.requestedPosts, 2);
    assert.equal(output.summary.successfulPosts, 1);
    assert.equal(output.summary.failedPosts, 1);
    assert.deepEqual(output.summary.failures, [
      {
        url: CANONICAL_URLS.bbb,
        reason: 'http_error',
        status: 404,
        message: 'HTTP 404',
      },
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('getPosts command bounds minReturnedPosts into the listing request limit', async () => {
  const requestedLimits: string[] = [];
  const fetchStub = createFetchStub((url) => {
    if (url.includes('/best.json')) {
      const parsed = new URL(url);
      requestedLimits.push(parsed.searchParams.get('limit') ?? '');
      return jsonResponse(buildListingPage([], null));
    }
    throw new Error(`unexpected url: ${url}`);
  });

  const originalFetch = globalThis.fetch;
  globalThis.fetch = fetchStub.stub;
  try {
    await execGetPosts({ minReturnedPosts: 0 });
    await execGetPosts({ minReturnedPosts: 500 });
    await execGetPosts({});

    assert.deepEqual(requestedLimits, ['1', '100', '20']);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('getPosts command keeps source, sort, and t validation behavior', async () => {
  await assert.rejects(
    () => execGetPosts({ source: 'unknown' }),
    /getPosts source must be one of/,
  );
  await assert.rejects(
    () => execGetPosts({ source: 'subreddit' }),
    /requires subreddit/,
  );
  await assert.rejects(
    () => execGetPosts({ source: 'user' }),
    /requires username/,
  );
  await assert.rejects(
    () => execGetPosts({ sort: 'controversial' }),
    /getPosts sort must be one of/,
  );
  await assert.rejects(
    () => execGetPosts({ t: 'decade' }),
    /getPosts t must be one of/,
  );
});
