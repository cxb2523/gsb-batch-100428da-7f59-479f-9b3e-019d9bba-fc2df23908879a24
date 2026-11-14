const http = require('http')
const fs = require('fs')
const path = require('path')
const { pipe, scrape, open, $, $$ } = require('../dist/index.js')

// Replica of test/examples.test.js example4 structure, served locally so it
// runs without external network. The root page links to authors, with Jane
// Austen linked twice to exercise noRevisit.
const authorPages = {
    '/author/Albert-Einstein/': { name: 'Albert Einstein', birthdate: 'March 14, 1879' },
    '/author/J-K-Rowling/': { name: 'J.K. Rowling', birthdate: 'July 31, 1965' },
    '/author/Jane-Austen/': { name: 'Jane Austen', birthdate: 'December 16, 1775' }
}

let server
let baseURL

beforeAll(async () => {
    await new Promise(resolve => {
        server = http.createServer((req, res) => {
            if (req.url === '/') {
                const links = [
                    '/author/Albert-Einstein/',
                    '/author/J-K-Rowling/',
                    '/author/Jane-Austen/',
                    '/author/Jane-Austen/' // duplicate author link, like the live page
                ].map(href => `<span class="author">x</span><a href="${href}">about</a>`).join('')
                res.end(`<html><body>${links}</body></html>`)
                return
            }
            const a = authorPages[req.url]
            if (a) {
                res.end(`<html><body><h3 class="author-title">${a.name}</h3><span class="author-born-date">${a.birthdate}</span></body></html>`)
                return
            }
            res.statusCode = 404
            res.end('nf')
        })
        server.listen(0, '127.0.0.1', () => {
            baseURL = `http://127.0.0.1:${server.address().port}`
            resolve()
        })
    })
})

afterAll(async () => {
    await new Promise(resolve => server.close(resolve))
})

test('example4-style crawl keeps every unique author and records the duplicate', async () => {
    const out = path.resolve(__dirname, 'example4-hermetic.jsonl')
    const config = {
        save: out,
        noRevisit: true,
        root: baseURL,
        options: { browser: { headless: 'shell' } },
        parse: pipe(
            open(),
            $$('.author + a'),
            open(),
            {
                name: $('h3.author-title'),
                birthdate: $('.author-born-date')
            }
        )
    }

    await scrape(config)

    const lines = fs.readFileSync(out, 'utf8').split('\n').filter(Boolean).map(JSON.parse)

    // The three unique authors are parsed exactly as before ...
    const records = lines.filter(l => !Array.isArray(l))
    expect(records).toEqual([
        { name: 'Albert Einstein', birthdate: 'March 14, 1879' },
        { name: 'J.K. Rowling', birthdate: 'July 31, 1965' },
        { name: 'Jane Austen', birthdate: 'December 16, 1775' }
    ])

    // ... and the repeated Jane Austen link becomes an identifiable skip record
    // carrying url + reason, without opening another page or breaking JSONL.
    const skips = lines.filter(l => Array.isArray(l))
    expect(skips).toEqual([
        [{ url: baseURL + '/author/Jane-Austen/', reason: 'already visited' }]
    ])

    fs.unlinkSync(out)
})
