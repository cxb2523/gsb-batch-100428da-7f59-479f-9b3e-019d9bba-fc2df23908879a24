import { registerHooks } from 'node:module';

/**
 * Redirects the bare `apify` import (and the `apify-mock-state` helper specifier used by
 * tests) to an in-memory mock, so detailRoute runs without the Apify SDK initializing
 * storage or touching the process. The mock owns the mutable state the route test drives.
 */
const apifyMockSource = `
export const mockState = {
    pushedData: [],
    remainingItems: 10,
};

export const Actor = {
    async pushData(item) {
        mockState.pushedData.push(item);
    },
};

export const log = {
    info() {},
    warning() {},
    error() {},
    debug() {},
};
`;

const MOCK_URL = 'test://mock-apify';

registerHooks({
    resolve(specifier, context, nextResolve) {
        if (specifier === 'apify' || specifier === 'apify-mock-state') {
            return {
                url: MOCK_URL,
                shortCircuit: true,
            };
        }
        return nextResolve(specifier, context);
    },
    load(url, context, nextLoad) {
        if (url === MOCK_URL) {
            return {
                format: 'module',
                source: apifyMockSource,
                shortCircuit: true,
            };
        }
        return nextLoad(url, context);
    },
});
