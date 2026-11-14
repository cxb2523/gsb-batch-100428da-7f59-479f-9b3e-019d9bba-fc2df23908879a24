/**
 * The `apify-mock-state` specifier exists only inside the test module loader
 * (test/helpers/apify-mock-loader.mjs), which maps it to the same in-memory module
 * as the mocked `apify` import. This declaration makes TypeScript aware of it.
 */
declare module 'apify-mock-state' {
    export const mockState: {
        pushedData: unknown[];
        remainingItems: number;
    };
}
