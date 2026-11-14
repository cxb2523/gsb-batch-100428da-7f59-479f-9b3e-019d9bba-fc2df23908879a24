/**
 * Possible final outcomes of a single request processed by the PhantomJS crawler.
 */
const OUTCOME_TYPES = Object.freeze({
    // The request was processed (the page was loaded and handled, possibly after some retries)
    SUCCESS: 'SUCCESS',
    // The page repeatedly failed to load and the configured maxPageRetryCount was exhausted
    PAGE_LOAD_RETRY_EXHAUSTED: 'PAGE_LOAD_RETRY_EXHAUSTED',
    // The PhantomJS process crashed MAX_CRASHES_PER_REQUEST times while handling the request
    PHANTOMJS_CRASH_LIMIT_EXCEEDED: 'PHANTOMJS_CRASH_LIMIT_EXCEEDED',
});

const ERROR_MESSAGE_MAX_LENGTH = 300;

/**
 * Makes a short one-line summary of the last error message from a (possibly multi-line) 'errorInfo' string.
 * Returns null if there is no error message.
 */
const summarizeErrorMessage = (errorInfo) => {
    if (typeof errorInfo !== 'string') return null;
    const lines = errorInfo
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter((line) => line.length > 0);
    if (lines.length === 0) return null;
    const summary = lines[lines.length - 1].replace(/\s+/g, ' ').trim();
    if (!summary) return null;
    return summary.length > ERROR_MESSAGE_MAX_LENGTH
        ? `${summary.slice(0, ERROR_MESSAGE_MAX_LENGTH)}...`
        : summary;
};

const copyRecord = (record) => (record ? { ...record } : null);

// Once a request is finalized, its record is only needed until the request is marked as handled
// and its outcome is pushed to the dataset. To avoid unbounded memory usage on large crawls,
// only this many finalized records are kept in memory (the oldest ones are discarded first).
const MAX_FINALIZED_RECORDS = 100000;

/**
 * Keeps track of the final outcome of every request handled by the PhantomCrawler.
 *
 * A record is created lazily on the first retry or crash (i.e. while the request is still being retried)
 * and only receives the final 'outcome' once the request really terminates. Until then,
 * getFinalOutcome() returns null so that in-progress requests never leak a final outcome.
 *
 * A request can only ever have a single final outcome - once 'outcome' is set, all subsequent
 * finalization attempts are ignored and the original outcome is preserved.
 */
class RequestOutcomeTracker {
    constructor() {
        // Maps String(request.id) to the outcome record
        this.recordsByRequestId = new Map();
        // Keys of finalized records, in the order in which they were finalized
        this.finalizedKeys = [];
        this.maxFinalizedRecords = MAX_FINALIZED_RECORDS;
    }

    _getRecord(request) {
        if (!request || request.id === undefined || request.id === null) return null;
        const key = String(request.id);
        let record = this.recordsByRequestId.get(key);
        if (!record) {
            record = {
                requestId: request.id,
                url: request.url || null,
                outcome: null,
                retryCount: 0,
                crashCount: 0,
                errorMessage: null,
            };
            this.recordsByRequestId.set(key, record);
        } else if (request.url) {
            record.url = request.url;
        }
        return record;
    }

    /**
     * Updates a non-final (in-progress) record. Never sets the final outcome.
     */
    _updateInProgress(request, { retryCount, crashCount, errorInfo } = {}) {
        const record = this._getRecord(request);
        if (!record || record.outcome) return copyRecord(record);
        if (typeof retryCount === 'number') record.retryCount = retryCount;
        if (typeof crashCount === 'number') record.crashCount = crashCount;
        if (errorInfo !== undefined) {
            const summary = summarizeErrorMessage(errorInfo);
            if (summary) record.errorMessage = summary;
        }
        return copyRecord(record);
    }

    /**
     * Records that the page load failed and the request will be retried.
     * The request is NOT finalized by this call.
     */
    recordPageLoadRetry(request, { retryCount, errorInfo } = {}) {
        return this._updateInProgress(request, { retryCount, errorInfo });
    }

    /**
     * Records that the PhantomJS process crashed and the request will be retried.
     * The request is NOT finalized by this call.
     */
    recordPhantomJsCrash(request, { crashCount, retryCount, errorInfo } = {}) {
        return this._updateInProgress(request, { crashCount, retryCount, errorInfo });
    }

    _finalize(request, outcome, { retryCount, crashCount, errorInfo } = {}) {
        const record = this._getRecord(request);
        if (!record) return null;
        // Only the first finalization wins, so that every request has exactly one final outcome
        if (record.outcome) return copyRecord(record);

        record.outcome = outcome;
        if (typeof retryCount === 'number') record.retryCount = retryCount;
        if (typeof crashCount === 'number') record.crashCount = crashCount;
        if (errorInfo !== undefined) {
            const summary = summarizeErrorMessage(errorInfo);
            if (summary) record.errorMessage = summary;
        }

        this.finalizedKeys.push(String(request.id));
        if (this.finalizedKeys.length > this.maxFinalizedRecords) {
            // Discard the oldest finalized records, they were already read and pushed to the dataset
            const oldestKey = this.finalizedKeys.shift();
            this.recordsByRequestId.delete(oldestKey);
        }

        return copyRecord(record);
    }

    /**
     * The request was processed successfully, possibly after some retries or crashes.
     */
    finalizeSuccess(request, { retryCount, crashCount, errorInfo } = {}) {
        return this._finalize(request, OUTCOME_TYPES.SUCCESS, { retryCount, crashCount, errorInfo });
    }

    /**
     * The page load failed and no more retries are allowed.
     */
    finalizePageLoadRetryExhausted(request, { retryCount, crashCount, errorInfo } = {}) {
        return this._finalize(request, OUTCOME_TYPES.PAGE_LOAD_RETRY_EXHAUSTED, { retryCount, crashCount, errorInfo });
    }

    /**
     * The PhantomJS process crashed MAX_CRASHES_PER_REQUEST times and the request is given up.
     */
    finalizePhantomJsCrashLimit(request, { retryCount, crashCount, errorInfo } = {}) {
        return this._finalize(request, OUTCOME_TYPES.PHANTOMJS_CRASH_LIMIT_EXCEEDED, { retryCount, crashCount, errorInfo });
    }

    /**
     * Returns a copy of the internal record for the request (final or in-progress),
     * or null if the request is unknown.
     */
    getRecord(requestId) {
        if (requestId === undefined || requestId === null) return null;
        return copyRecord(this.recordsByRequestId.get(String(requestId)));
    }

    /**
     * Returns a copy of the final outcome record for the request, or null if the request
     * is unknown or is still being retried.
     */
    getFinalOutcome(requestId) {
        if (requestId === undefined || requestId === null) return null;
        const record = this.recordsByRequestId.get(String(requestId));
        return record && record.outcome ? copyRecord(record) : null;
    }

    hasFinalOutcome(requestId) {
        return this.getFinalOutcome(requestId) !== null;
    }
}

module.exports = {
    OUTCOME_TYPES,
    RequestOutcomeTracker,
    summarizeErrorMessage,
};
