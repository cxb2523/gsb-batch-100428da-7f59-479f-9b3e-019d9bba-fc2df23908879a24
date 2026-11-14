const { assert } = require('chai');
const _ = require('underscore');
const PhantomCrawler = require('../src/phantom_crawler');
const { OUTCOME_TYPES, RequestOutcomeTracker, summarizeErrorMessage } = require('../src/request_outcome');

const SLAVE_ID = 1001;
const MAX_CRASHES_PER_REQUEST = 3;

/**
 * Builds a legacy request object as it exists in the crawler (i.e. after `requestRawToLegacy()`),
 * with only the fields relevant for the retry/crash logic and the dataset output item.
 */
const makeRequest = (overrides = {}) => ({
    id: 'request-id-1',
    url: 'https://www.example.com/some-page',
    uniqueKey: 'https://www.example.com/some-page',
    method: 'GET',
    postData: null,
    contentType: null,
    errorInfo: '',
    loadErrorCode: null,
    loadingStartedAt: null,
    loadedUrl: null,
    loadingFinishedAt: null,
    pageFunctionStartedAt: null,
    pageFunctionFinishedAt: null,
    pageFunctionResult: null,
    downloadedBytes: 0,
    responseStatus: null,
    responseHeaders: null,
    _retryCount: 0,
    ...overrides,
});

/**
 * Creates a PhantomCrawler with the external dependencies stubbed, so that the slave message
 * handling logic can run locally without PhantomJS, a real request queue or the Apify platform.
 */
const createCrawler = ({ maxPageRetryCount = 2 } = {}) => {
    const reclaimedRequests = [];
    const handledRequests = [];
    const pushedItems = [];

    const requestQueue = {
        reclaimRequest: async (requestRaw) => {
            reclaimedRequests.push(requestRaw.retryCount || 0);
        },
        markRequestHandled: async (requestRaw) => {
            handledRequests.push(requestRaw);
            return { wasAlreadyHandled: false };
        },
    };
    const dataset = {
        pushData: async (item) => {
            pushedItems.push(_.clone(item));
        },
    };

    const crawler = new PhantomCrawler({
        input: {
            maxPageRetryCount,
            maxParallelRequests: 4,
            clickableElementsSelector: 'DONT_CLICK_ANYTHING',
        },
        requestQueue,
        dataset,
    });

    // The crawler only processes slave messages while running
    crawler.isRunning = true;

    // PageManager keeps its own counters, initialize them so that markRequestHandled() can update them
    crawler.pageManager.pagesCrawled = 0;
    crawler.pageManager.pagesOutputted = 0;
    crawler.pageManager.pagesInQueue = 1;

    crawler.liveViewServer.hasClients = () => false;

    return { crawler, reclaimedRequests, handledRequests, pushedItems };
};

/**
 * Registers a slave that has already fetched the given request, so that a subsequent
 * 'markRequestHandled' message from it is accepted by the crawler.
 */
const registerSlaveWithFetchedRequest = (crawler, request) => {
    const slave = {
        id: SLAVE_ID,
        isBootstrapper: false,
        isExited: false,
        lastMessageAt: new Date(),
        lastFetchedRequest: _.clone(request),
        proxy: null,
    };
    crawler.slaves[slave.id] = slave;
    return slave;
};

/**
 * Sends a message from a slave through the regular slave message handling code path
 * and waits until the message is fully processed.
 */
const sendMessageFromSlave = async (crawler, slave, message) => {
    crawler.tasksFromSlaves.add({
        slave,
        message,
        httpResponse: { end: () => {}, writeHead: () => {} },
    });
    await crawler._handleNextTaskFromSlave();
};

/**
 * Simulates a 'markRequestHandled' message for the slave's last fetched request
 * and returns the parsed result of the request as pushed to the dataset, if any.
 */
const markHandledBySlave = async (crawler, slave, requestOverrides) => {
    const original = slave.lastFetchedRequest;
    const message = {
        messageType: 'markRequestHandled',
        request: makeRequest({
            id: original.id,
            url: original.url,
            ...requestOverrides,
        }),
    };
    await sendMessageFromSlave(crawler, slave, message);
    delete crawler.slaves[slave.id];
};

describe('PhantomCrawler request outcomes', function () {
    this.timeout(10000);

    describe('page load retries', () => {
        it('retries on the first failure, then records SUCCESS with the retry count', async () => {
            const { crawler, reclaimedRequests, handledRequests, pushedItems } = createCrawler({ maxPageRetryCount: 2 });
            const request = makeRequest();

            // First attempt: the page fails to load -> the request is reclaimed for a retry
            const slave1 = registerSlaveWithFetchedRequest(crawler, request);
            await markHandledBySlave(crawler, slave1, {
                loadErrorCode: 999,
                errorInfo: 'First attempt failed: net::ERR_CONNECTION_RESET\n',
            });

            assert.lengthOf(reclaimedRequests, 1, 'The failed page must be reclaimed for a retry');
            assert.lengthOf(handledRequests, 0, 'A request that is being retried must not be marked as handled');
            assert.lengthOf(pushedItems, 0, 'A request that is being retried must not be outputted');
            assert.isNull(
                crawler.requestOutcomeTracker.getFinalOutcome(request.id),
                'A request that is being retried must not have a final outcome',
            );

            // Second attempt: the page loads -> success with retryCount 1
            const slave2 = registerSlaveWithFetchedRequest(crawler, { ...request, _retryCount: 1 });
            await markHandledBySlave(crawler, slave2, {
                loadErrorCode: null,
                errorInfo: '',
                pageFunctionResult: { foo: 'bar' },
            });

            assert.lengthOf(handledRequests, 1);
            assert.lengthOf(pushedItems, 1);

            const outcome = crawler.requestOutcomeTracker.getFinalOutcome(request.id);
            assert.isNotNull(outcome, 'A handled request must have a final outcome');
            assert.equal(outcome.requestId, request.id);
            assert.equal(outcome.url, request.url);
            assert.equal(outcome.outcome, OUTCOME_TYPES.SUCCESS);
            assert.equal(outcome.retryCount, 1);
            // The last failure of the request happened during the first attempt
            assert.equal(outcome.errorMessage, 'First attempt failed: net::ERR_CONNECTION_RESET');

            assert.deepEqual(pushedItems[0].requestOutcome, outcome, 'The outcome must be part of the dataset item');
            assert.deepEqual(pushedItems[0].pageFunctionResult, { foo: 'bar' });
        });

        it('gives up after the retry limit and records PAGE_LOAD_RETRY_EXHAUSTED with the last error', async () => {
            const maxPageRetryCount = 2;
            const { crawler, reclaimedRequests, handledRequests, pushedItems } = createCrawler({ maxPageRetryCount });
            const request = makeRequest();

            // The first maxPageRetryCount failures are retried
            for (let attempt = 1; attempt <= maxPageRetryCount; attempt++) {
                const slave = registerSlaveWithFetchedRequest(crawler, { ...request, _retryCount: attempt - 1 });
                await markHandledBySlave(crawler, slave, {
                    loadErrorCode: 999,
                    errorInfo: `Attempt ${attempt} failed: page load timeout\n`,
                });

                assert.lengthOf(reclaimedRequests, attempt);
                assert.isNull(
                    crawler.requestOutcomeTracker.getFinalOutcome(request.id),
                    `Retry ${attempt} must not produce a final outcome`,
                );
            }

            // The final attempt fails too -> retries are exhausted and the request is given up
            const slave = registerSlaveWithFetchedRequest(crawler, { ...request, _retryCount: maxPageRetryCount });
            await markHandledBySlave(crawler, slave, {
                loadErrorCode: 999,
                errorInfo: 'Last attempt failed: net::ERR_NAME_NOT_RESOLVED\n',
            });

            assert.lengthOf(reclaimedRequests, maxPageRetryCount, 'The last attempt must not be reclaimed');
            assert.lengthOf(handledRequests, 1, 'The given-up request must be marked as handled');

            const outcome = crawler.requestOutcomeTracker.getFinalOutcome(request.id);
            assert.isNotNull(outcome);
            assert.equal(outcome.requestId, request.id);
            assert.equal(outcome.url, request.url);
            assert.equal(outcome.outcome, OUTCOME_TYPES.PAGE_LOAD_RETRY_EXHAUSTED);
            assert.equal(outcome.retryCount, maxPageRetryCount);
            assert.equal(outcome.crashCount, 0);
            assert.equal(outcome.errorMessage, 'Last attempt failed: net::ERR_NAME_NOT_RESOLVED');

            assert.lengthOf(pushedItems, 1);
            assert.deepEqual(pushedItems[0].requestOutcome, outcome);
            assert.match(pushedItems[0].errorInfo, /Page load failed 3 times, giving up/);
        });

        it('reports PAGE_LOAD_RETRY_EXHAUSTED with zero retries when maxPageRetryCount is 0', async () => {
            const { crawler, reclaimedRequests, handledRequests, pushedItems } = createCrawler({ maxPageRetryCount: 0 });
            const request = makeRequest();

            const slave = registerSlaveWithFetchedRequest(crawler, request);
            await markHandledBySlave(crawler, slave, {
                loadErrorCode: 999,
                errorInfo: 'Page could not be opened\n',
            });

            assert.lengthOf(reclaimedRequests, 0, 'Retrying is disabled, so the request must not be reclaimed');
            assert.lengthOf(handledRequests, 1);

            const outcome = crawler.requestOutcomeTracker.getFinalOutcome(request.id);
            assert.equal(outcome.outcome, OUTCOME_TYPES.PAGE_LOAD_RETRY_EXHAUSTED);
            assert.equal(outcome.retryCount, 0);
            assert.equal(outcome.errorMessage, 'Page could not be opened');
            assert.deepEqual(pushedItems[0].requestOutcome, outcome);
        });
    });

    describe('PhantomJS crashes', () => {
        it('retries crashes without a final outcome and gives up at MAX_CRASHES_PER_REQUEST', async () => {
            const { crawler, reclaimedRequests, handledRequests, pushedItems } = createCrawler();
            // NOTE: the very same request object is reused for all attempts, because the request queue
            // keeps the crash counter in the request between attempts (see requestRawToLegacy())
            const request = makeRequest({ errorInfo: 'Page processing crashed\n' });

            // The first crashes lead to retries
            for (let crash = 1; crash < MAX_CRASHES_PER_REQUEST; crash++) {
                await crawler.handleRequestAfterSlaveExit(request, SLAVE_ID);

                assert.isNull(
                    crawler.requestOutcomeTracker.getFinalOutcome(request.id),
                    `Crash ${crash} must not produce a final outcome`,
                );
                assert.equal(crawler.requestOutcomeTracker.getRecord(request.id).crashCount, crash);
                assert.equal(reclaimedRequests.length, crash, 'The crashed request must be reclaimed for a retry');
                assert.lengthOf(handledRequests, 0, 'A request that is being retried must not be marked as handled');
            }

            // The last crash makes the crawler give up on the request
            await crawler.handleRequestAfterSlaveExit(request, SLAVE_ID);

            assert.lengthOf(reclaimedRequests, MAX_CRASHES_PER_REQUEST - 1);
            assert.lengthOf(handledRequests, 1);
            assert.lengthOf(pushedItems, 1);

            const outcome = crawler.requestOutcomeTracker.getFinalOutcome(request.id);
            assert.isNotNull(outcome);
            assert.equal(outcome.requestId, request.id);
            assert.equal(outcome.url, request.url);
            assert.equal(outcome.outcome, OUTCOME_TYPES.PHANTOMJS_CRASH_LIMIT_EXCEEDED);
            assert.equal(outcome.crashCount, MAX_CRASHES_PER_REQUEST);
            assert.equal(outcome.retryCount, 0);
            assert.equal(outcome.errorMessage, 'Page processing crashed');

            assert.deepEqual(pushedItems[0].requestOutcome, outcome);
            assert.match(pushedItems[0].errorInfo, /Crawler crashed 3 times/);
        });

        it('keeps the page load retry count when the request later crashes', async () => {
            const { crawler } = createCrawler({ maxPageRetryCount: 5 });
            const request = makeRequest({ _retryCount: 2, errorInfo: 'Page load failed repeatedly\n' });

            for (let crash = 1; crash <= MAX_CRASHES_PER_REQUEST; crash++) {
                await crawler.handleRequestAfterSlaveExit(request, SLAVE_ID);
            }

            const outcome = crawler.requestOutcomeTracker.getFinalOutcome(request.id);
            assert.equal(outcome.outcome, OUTCOME_TYPES.PHANTOMJS_CRASH_LIMIT_EXCEEDED);
            assert.equal(outcome.crashCount, MAX_CRASHES_PER_REQUEST);
            assert.equal(outcome.retryCount, 2);
        });

        it('does not emit a final outcome while the crashed request is still being retried', async () => {
            const { crawler } = createCrawler();
            const request = makeRequest();

            await crawler.handleRequestAfterSlaveExit(_.clone(request), SLAVE_ID);

            const record = crawler.requestOutcomeTracker.getRecord(request.id);
            assert.isNotNull(record, 'The in-progress request should still be tracked');
            assert.isNull(record.outcome);
            assert.equal(record.crashCount, 1);
            assert.isNull(crawler.requestOutcomeTracker.getFinalOutcome(request.id));
        });

        it('records a successful outcome with the crash count when the request succeeds after crashes', async () => {
            const { crawler, pushedItems } = createCrawler();
            const request = makeRequest();

            // The request crashed twice, but was retried and finally succeeded
            await crawler.handleRequestAfterSlaveExit(request, SLAVE_ID);
            await crawler.handleRequestAfterSlaveExit(request, SLAVE_ID);

            const slave = registerSlaveWithFetchedRequest(crawler, request);
            await markHandledBySlave(crawler, slave, { loadErrorCode: null, errorInfo: '' });

            const outcome = crawler.requestOutcomeTracker.getFinalOutcome(request.id);
            assert.equal(outcome.outcome, OUTCOME_TYPES.SUCCESS);
            assert.equal(outcome.crashCount, 2);
            assert.equal(outcome.retryCount, 0);
            assert.deepEqual(pushedItems[0].requestOutcome, outcome);
        });
    });

    describe('request outcome tracker', () => {
        it('only ever produces a single final outcome per request', () => {
            const { crawler } = createCrawler();
            const tracker = crawler.requestOutcomeTracker;
            const request = makeRequest();

            const first = tracker.finalizeSuccess(request, { retryCount: 1 });
            assert.equal(first.outcome, OUTCOME_TYPES.SUCCESS);

            // Any subsequent update or finalization attempt must preserve the first final outcome
            tracker.finalizePageLoadRetryExhausted(request, { retryCount: 2 });
            tracker.finalizePhantomJsCrashLimit(request, { crashCount: 3 });
            tracker.recordPageLoadRetry(request, { retryCount: 3 });

            const finalOutcome = tracker.getFinalOutcome(request.id);
            assert.equal(finalOutcome.outcome, OUTCOME_TYPES.SUCCESS);
            assert.equal(finalOutcome.retryCount, 1);
            assert.equal(finalOutcome.crashCount, 0);
        });

        it('summarizes multiline error info using the last non-empty line', () => {
            assert.isNull(summarizeErrorMessage(''));
            assert.isNull(summarizeErrorMessage('\n\n'));
            assert.isNull(summarizeErrorMessage(null));
            assert.equal(summarizeErrorMessage('First error\n  Second   error  \n\n'), 'Second error');
            assert.isBelow(summarizeErrorMessage('x'.repeat(1000)).length, 1000);
        });

        it('discards the oldest finalized records once the in-memory limit is reached', () => {
            const tracker = new RequestOutcomeTracker();
            tracker.maxFinalizedRecords = 2;

            const requests = [1, 2, 3].map((i) => makeRequest({ id: `request-id-${i}` }));
            requests.forEach((request) => tracker.finalizeSuccess(request, { retryCount: 0 }));

            assert.isNull(tracker.getFinalOutcome(requests[0].id), 'The oldest finalized record should be discarded');
            assert.isNotNull(tracker.getFinalOutcome(requests[1].id));
            assert.isNotNull(tracker.getFinalOutcome(requests[2].id));
        });
    });
});
