import { ElementHandle, Page, WaitForOptions } from 'puppeteer'
import { isIterable } from './isIterable'
import { attr, text } from './property'
import { log } from './log'



export const SKIP_REASON_ALREADY_VISITED = 'already visited'

export interface SkipRecord {
    url: string
    reason: string
}

// Marker on the one-item iterable returned for a skipped URL. Later pipe stages
// ($/$$/text/...) detect it and pass the record through untouched, so no browser
// Page API is ever called for a page that was never opened.
export const SKIP_ITERABLE = Symbol('scrapyteer.skipIterable')

export interface SkipIterable {
    [SKIP_ITERABLE]: SkipRecord
}

function makeSkipIterable(record: SkipRecord): SkipIterable & AsyncIterable<SkipRecord> & Iterable<SkipRecord> {
    return {
        [SKIP_ITERABLE]: record,
        [Symbol.iterator]: function* () {
            yield record
        },
        [Symbol.asyncIterator]: async function* () {
            yield record
        }
    }
}

export function isSkipIterable(value: any): value is SkipIterable {
    return !!value && typeof value === 'object' && SKIP_ITERABLE in value
}

export function open(options: WaitForOptions & { referer?: string } = {}): (link: string|ElementHandle) => Promise<(f: (page: Page) => any) => any>  {
    return async (link: string|ElementHandle = '/'): Promise<(f: (page: Page) => any) => any> => {
        let url = ''
        if (typeof link === 'string') {
            url = link
        } else {
            const tag = (await attr('tagName', link)).toString().toUpperCase()
            url = tag == 'A' ? await attr('href', link) : await text(link)
        }
        url = composeURL(url)
        if (global.scrapyteer.visited) {
            if (global.scrapyteer.visited.has(url)) {
                // Never create a browser page for a skipped (already visited) URL.
                log('Skip', url, '(' + SKIP_REASON_ALREADY_VISITED + ')')
                const record: SkipRecord = { url, reason: SKIP_REASON_ALREADY_VISITED }
                const skip = makeSkipIterable(record)
                return async (f?: (page: Page) => any) => {
                    if (typeof f === 'function') {
                        // Nested open(): pipe hands the rest of the workflow here.
                        // Run it with the marked skip iterable in place of a Page.
                        // pipe() recognizes the marker and short-circuits every later
                        // stage, so no Page API is touched; the output layer unwraps it.
                        return await (f as (p: any) => any)(skip)
                    }
                    // Top-level open(): return the same marked iterable. pipe() stops
                    // applying further stages to it, while json/jsonl/console can
                    // consume it as iterable data (a bare object would make JSONL fail
                    // as non-iterable).
                    return skip
                }
            }
            global.scrapyteer.visited.add(url)
        }
        const page = await global.scrapyteer.browser.newPage() as Page
        page.setDefaultNavigationTimeout(0)
        let closed = false
        const closePage = async () => {
            if (closed) {
                return
            }
            closed = true
            log('Close', url)
            await page.close()
        }
        return async f => {
            log('Opening', url, '...')
            let res
            try {
                const resp = await page.goto(url, options)
                log(resp.ok() ? 'successful' : 'failed', 'with status', resp.status())
                res = await f(page)
            } catch (e) {
                // Navigation failure or a parser error before iteration starts:
                // the page still has to be closed.
                await closePage()
                throw e
            }
            if (isIterable(res)) {
                const it = typeof res[Symbol.asyncIterator] === 'function' ? res[Symbol.asyncIterator]() : res[Symbol.iterator]()
                return {
                    next: async () => {
                        try {
                            const {done, value} = await it.next()
                            if (done) {
                                await closePage()
                            }
                            return {done, value}
                        } catch (e) {
                            await closePage()
                            throw e
                        }
                    },
                    // Called when the consumer stops pulling early (break, throw, cleanup)
                    return: async (value?: any) => {
                        await closePage()
                        if (typeof it.return === 'function') {
                            return await it.return(value)
                        }
                        return { value, done: true }
                    },
                    throw: async (e?: any) => {
                        await closePage()
                        if (typeof it.throw === 'function') {
                            return await it.throw(e)
                        }
                        throw e
                    },
                    [Symbol.asyncIterator]: function() { return this }
                }
            }
            await closePage()
            return res
        }
    }
}

export function composeURL(url: string): string {
    return url.startsWith('http') ? url : global.scrapyteer.rootURL + url
}
