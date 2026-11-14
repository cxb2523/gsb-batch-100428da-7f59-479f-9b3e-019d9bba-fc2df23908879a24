const http = require('http')
const fs = require('fs')
const path = require('path')
const puppeteer = require('puppeteer')
const { pipe, scrape, open, $, $$, text, SKIP_REASON_ALREADY_VISITED } = require('../dist/index.js')

let server
let port
let baseURL

const pages = {
    '/p1': `<html><body><h1>P1</h1><div class="item">p1-a</div><div class="item">p1-b</div></body></html>`,
    '/p2': `<html><body><h1>P2</h1><div class="item">p2-a</div><div class="item">p2-b</div></body></html>`,
    '/p3': `<html><body><h1>P3</h1><div class="item">p3-a</div><div class="item">p3-b</div></body></html>`,
    '/throw': `<html><body><div class="item">t-a</div><div class="item">t-b</div><div class="item">t-c</div></body></html>`
}

beforeAll(async () => {
    await new Promise(resolve => {
        server = http.createServer((req, res) => {
            res.writeHead(pages[req.url] ? 200 : 404, { 'Content-Type': 'text/html' })
            res.end(pages[req.url] || 'not found')
        })
        server.listen(0, '127.0.0.1', () => {
            port = server.address().port
            baseURL = `http://127.0.0.1:${port}`
            resolve()
        })
    })
})

afterAll(async () => {
    await new Promise(resolve => server.close(resolve))
})

// Wraps browser.newPage/page.close so tests can count created pages and verify
// every created page is eventually closed, without depending on live websites.
// Must return a non-nil value: pipe() short-circuits a nil first argument.
function trackPages(stats) {
    const browser = global.scrapyteer.browser
    const origNewPage = browser.newPage.bind(browser)
    browser.newPage = async () => {
        const page = await origNewPage()
        stats.created++
        const origClose = page.close.bind(page)
        page.close = async () => {
            stats.closed++
            return origClose()
        }
        return page
    }
    return true
}

function loadLines(fpath, del = true) {
    const lines = fs.readFileSync(fpath, 'utf8').split('\n').filter(l => l.length).map(JSON.parse)
    if (del) {
        fs.unlinkSync(fpath)
    }
    return lines
}

// Use the headless-shell build that puppeteer installs by default and that is
// fully self-contained; these tests never need a visible/full Chrome window.
const browser = { headless: 'shell' }


test('duplicate URL is skipped with a record and no extra page is created', async () => {
    const out = path.resolve(__dirname, 'open-skip.jsonl')
    const stats = { created: 0, closed: 0 }

    const config = {
        save: out,
        noRevisit: true,
        root: baseURL,
        options: { browser },
        parse: pipe(
            () => trackPages(stats),
            ['/p1', '/p2', '/p1'],
            open(),
            $$('.item'),
            text
        )
    }

    await scrape(config)

    // Only the two distinct URLs open browser pages; the repeated /p1 must not.
    expect(stats.created).toBe(2)
    expect(stats.closed).toBe(2)

    // The skip record is an iterable like every other open() result, so it is
    // emitted as a one-element array on its own JSONL line and carries url+reason.
    expect(loadLines(out)).toEqual([
        ['p1-a', 'p1-b'],
        ['p2-a', 'p2-b'],
        [{ url: baseURL + '/p1', reason: SKIP_REASON_ALREADY_VISITED }]
    ])
})


test('page is closed when parsing throws mid-iteration', async () => {
    const stats = { created: 0, closed: 0 }
    let count = 0

    const config = {
        save: console, // no file handle left open when the error aborts the run
        root: baseURL,
        options: { browser },
        parse: pipe(
            () => trackPages(stats),
            () => '/throw',
            open(),
            $$('.item'),
            text,
            value => {
                count++
                if (count === 2) {
                    throw new Error('boom mid-iteration')
                }
                return value
            }
        )
    }

    await expect(scrape(config)).rejects.toThrow('boom mid-iteration')

    // The page opened for /throw must be closed even though the iterator never
    // reached done and the error propagated out of scrape().
    expect(stats.created).toBe(1)
    expect(stats.closed).toBe(1)
})


test('page is closed when parsing throws before iteration starts', async () => {
    const stats = { created: 0, closed: 0 }

    const config = {
        save: console,
        root: baseURL,
        options: { browser },
        parse: pipe(
            () => trackPages(stats),
            () => '/p1',
            open(),
            () => {
                throw new Error('boom before iteration')
            },
            $$('.item')
        )
    }

    await expect(scrape(config)).rejects.toThrow('boom before iteration')

    expect(stats.created).toBe(1)
    expect(stats.closed).toBe(1)
})


test('page is closed when navigation itself fails', async () => {
    const stats = { created: 0, closed: 0 }

    const config = {
        save: console,
        root: baseURL,
        options: { browser },
        // Port 1 is closed, so page.goto() rejects with a network error.
        parse: pipe(
            () => trackPages(stats),
            () => 'http://127.0.0.1:1/unreachable',
            open(),
            $$('.item'),
            text
        )
    }

    await expect(scrape(config)).rejects.toThrow()

    // A page was created for the attempt and must still be closed when goto fails.
    expect(stats.created).toBe(1)
    expect(stats.closed).toBe(1)
})


test('page is closed when the consumer stops iterating early', async () => {
    // Drive open() directly and abandon its result iterator after the first
    // item, the way a for-await `break` does (it calls the iterator's return()).
    const browser = await puppeteer.launch({ headless: 'shell' })
    const prev = global.scrapyteer
    global.scrapyteer = { rootURL: baseURL, browser, log: false, visited: null }
    const pages = await browser.pages()
    try {
        const opener = await open()('/p1')
        const iterable = await opener(async () => {
            async function* gen() {
                yield 'a'
                yield 'b'
                yield 'c'
            }
            return gen()
        })

        for await (const value of iterable) {
            if (value === 'a') {
                break // leave 'b' and 'c' unconsumed
            }
        }

        // The single page created by open() must be gone even though the
        // iterator never reached done.
        const remaining = await browser.pages()
        expect(remaining.length).toBe(pages.length)
    } finally {
        global.scrapyteer = prev
        await browser.close()
    }
})


test('normal pagination results are unaffected', async () => {
    const out = path.resolve(__dirname, 'open-pagination.jsonl')
    const stats = { created: 0, closed: 0 }

    const config = {
        save: out,
        root: baseURL,
        options: { browser },
        parse: pipe(
            () => trackPages(stats),
            ['/p1', '/p2', '/p3'],
            open(),
            $$('.item'),
            text
        )
    }

    await scrape(config)

    expect(stats.created).toBe(3)
    expect(stats.closed).toBe(3)
    expect(loadLines(out)).toEqual([
        ['p1-a', 'p1-b'],
        ['p2-a', 'p2-b'],
        ['p3-a', 'p3-b']
    ])
})


test('skip records coexist with normal data in json output', async () => {
    const out = path.resolve(__dirname, 'open-skip.json')
    const stats = { created: 0, closed: 0 }

    const config = {
        save: out,
        noRevisit: true,
        root: baseURL,
        options: { browser },
        parse: pipe(
            () => trackPages(stats),
            ['/p1', '/p1'],
            open(),
            $$('.item'),
            text
        )
    }

    await scrape(config)

    expect(stats.created).toBe(1)
    expect(stats.closed).toBe(1)
    expect(JSON.parse(fs.readFileSync(out, 'utf8'))).toEqual([
        ['p1-a', 'p1-b'],
        [{ url: baseURL + '/p1', reason: SKIP_REASON_ALREADY_VISITED }]
    ])
    fs.unlinkSync(out)
})


test('skip records are written to console output without iterable errors', async () => {
    const logs = []
    const origLog = console.log
    console.log = (...args) => logs.push(args.map(String).join(' '))
    try {
        const config = {
            save: console,
            noRevisit: true,
            root: baseURL,
        options: { browser },
            parse: pipe(
                ['/p2', '/p2'],
                open(),
                $$('.item'),
                text
            )
        }

        await scrape(config)
    } finally {
        console.log = origLog
    }

    // console output is one JSON document containing normal data and the skip record
    expect(logs.map(line => JSON.parse(line))).toEqual([
        [
            ['p2-a', 'p2-b'],
            [{ url: baseURL + '/p2', reason: SKIP_REASON_ALREADY_VISITED }]
        ]
    ])
})
