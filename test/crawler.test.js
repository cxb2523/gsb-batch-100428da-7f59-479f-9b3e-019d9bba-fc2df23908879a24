'use strict'

/*
 * Local, dependency-free tests for the crawler's link queueing and
 * completion behaviour. They use plain node:test plus a local HTTP server,
 * so `npm test` works without Elasticsearch/Neo4j or network access.
 */
const { test } = require('node:test')
const assert = require('node:assert')
const http = require('node:http')

const Crawler = require('../crawler/crawler.js')

const CRAWL_TIMEOUT_MS = 15000

function html (body) {
  return '<!doctype html><html><head><title>t</title></head><body>' + body + '</body></html>'
}

/*
 * Start a local HTTP server. `routes` maps a pathname to either a string of
 * HTML or a function (req, res, hits) that handles the request itself.
 * Every request is recorded in `hits` (pathname -> count).
 */
function startServer (routes) {
  const hits = new Map()
  const server = http.createServer(function (req, res) {
    const pathname = new URL(req.url, 'http://placeholder').pathname
    hits.set(pathname, (hits.get(pathname) || 0) + 1)
    const route = routes[pathname]
    if (typeof route === 'function') {
      route(req, res, hits)
      return
    }
    if (typeof route === 'string') {
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' })
      res.end(route)
      return
    }
    res.writeHead(404, { 'Content-Type': 'text/html; charset=utf-8' })
    res.end(html('<p>not found</p>'))
  })

  return new Promise(function (resolve, reject) {
    server.once('error', reject)
    // No explicit host: this listens on the loopback interfaces used by both
    // 127.0.0.1 and localhost, which lets tests use a distinct hostname.
    server.listen(0, function () {
      resolve({
        server: server,
        port: server.address().port,
        hits: hits,
        hitCount: function (pathname) { return hits.get(pathname) || 0 },
        totalHits: function () {
          let total = 0
          hits.forEach(function (count) { total += count })
          return total
        },
        close: function () {
          return new Promise(function (resolveClose) { server.close(resolveClose) })
        }
      })
    })
  })
}

/*
 * Crawl `rootUrl` and resolve once onAllFinished has been called. The promise
 * additionally resolves only after a short grace period so that a second,
 * erroneous invocation of onAllFinished is observed by the assertions.
 */
function crawlAndCollect (rootUrl, options) {
  const settings = options || {}
  const depth = settings.depth || 3
  return new Promise(function (resolve, reject) {
    const crawler = new Crawler().configure({
      depth: depth,
      maxConcurrentRequests: settings.maxConcurrentRequests || 5000,
      maxRequestsPerSecond: settings.maxRequestsPerSecond || 5000
    })

    const result = {
      crawler: crawler,
      succeeded: [],
      failed: [],
      finishedCalls: 0,
      finishedUrls: null
    }
    let timer = null

    function finish () {
      if (timer) {
        clearTimeout(timer)
        timer = null
      }
      setTimeout(function () { resolve(result) }, 100)
    }

    timer = setTimeout(function () {
      reject(new Error('crawl did not finish within ' + CRAWL_TIMEOUT_MS + 'ms'))
    }, CRAWL_TIMEOUT_MS)

    // shouldCrawl() reads the config object handed to crawl(), so the list
    // options have to travel with it.
    const crawlConfig = { url: rootUrl, depth: depth }
    if (settings.whiteList) crawlConfig.whiteList = settings.whiteList
    if (settings.blackList) crawlConfig.blackList = settings.blackList

    crawler.crawl(
      crawlConfig,
      function onSuccess (page) { result.succeeded.push(page) },
      function onFailure (page) { result.failed.push(page) },
      function onAllFinished (urls) {
        result.finishedCalls++
        result.finishedUrls = urls
        if (result.finishedCalls === 1) finish()
      }
    )
  })
}

function hostsOf (pageList) {
  return new Set(pageList.map(function (page) { return new URL(page.url).hostname }))
}

test('links on other hosts are skipped and never crawled', async function (t) {
  const external = await startServer({
    '/external': html('<p>external</p>')
  })
  t.after(function () { return external.close() })

  const externalBase = 'http://localhost:' + external.port
  const site = await startServer({
    '/': html(
      '<a href="/local">local</a>' +
            '<a href="' + externalBase + '/external">external</a>' +
            '<a href="//localhost:' + external.port + '/external">protocol relative</a>'
    ),
    '/local': html('<a href="/">home</a>')
  })
  t.after(function () { return site.close() })

  const siteBase = 'http://127.0.0.1:' + site.port
  const result = await crawlAndCollect(siteBase + '/')

  assert.strictEqual(result.finishedCalls, 1, 'onAllFinished must be called exactly once')

  // The crawler must not even issue requests to the other host.
  assert.strictEqual(external.totalHits(), 0, 'no request should reach the external host')
  assert.strictEqual(site.hitCount('/'), 1)
  assert.strictEqual(site.hitCount('/local'), 1)

  const reportedUrls = result.succeeded.map(function (page) { return page.url })
  assert.deepStrictEqual(
    reportedUrls.slice().sort(),
    [siteBase + '/', siteBase + '/local'].sort()
  )
  assert.deepStrictEqual(hostsOf(result.succeeded), new Set(['127.0.0.1']))
})

test('duplicate links are queued and crawled only once', async function (t) {
  const site = await startServer({
    '/': html(
      '<a href="/page2">one</a>' +
            '<a href="/page2">two</a>' +
            '<a href="/page2#fragment">three</a>' +
            '<a href="/page3">page3</a>'
    ),
    // Two different pages link back to the same other page.
    '/page2': html('<a href="/page3">page3</a><a href="/">home</a>'),
    '/page3': html('<a href="/page2">page2</a><a href="/page3">self</a>')
  })
  t.after(function () { return site.close() })

  const siteBase = 'http://127.0.0.1:' + site.port
  const result = await crawlAndCollect(siteBase + '/')

  assert.strictEqual(result.finishedCalls, 1)

  assert.strictEqual(site.hitCount('/'), 1)
  assert.strictEqual(site.hitCount('/page2'), 1, '/page2 must be fetched exactly once')
  assert.strictEqual(site.hitCount('/page3'), 1, '/page3 must be fetched exactly once')

  const reportedUrls = result.succeeded.map(function (page) { return page.url }).sort()
  const expected = [
    siteBase + '/',
    siteBase + '/page2',
    siteBase + '/page3'
  ].sort()
  assert.deepStrictEqual(reportedUrls, expected)
  assert.deepStrictEqual(result.finishedUrls.slice().sort(), expected)
})

test('a link loop terminates and reports the site once', async function (t) {
  const site = await startServer({
    '/a': html('<a href="/b">b</a>'),
    '/b': html('<a href="/a">a</a><a href="/b">b</a>')
  })
  t.after(function () { return site.close() })

  const siteBase = 'http://127.0.0.1:' + site.port
  const result = await crawlAndCollect(siteBase + '/a')

  assert.strictEqual(result.finishedCalls, 1)
  assert.strictEqual(site.totalHits(), 2)
  assert.deepStrictEqual(
    result.finishedUrls.slice().sort(),
    [siteBase + '/a', siteBase + '/b'].sort()
  )
})

test('a same-host link redirecting to another host is not counted as a result', async function (t) {
  const external = await startServer({
    '/target': html('<p>external target</p>')
  })
  t.after(function () { return external.close() })

  const externalBase = 'http://localhost:' + external.port
  const site = await startServer({
    '/': html('<a href="/away">away</a>'),
    '/away': function (req, res) {
      res.writeHead(302, { Location: externalBase + '/target' })
      res.end()
    }
  })
  t.after(function () { return site.close() })

  const siteBase = 'http://127.0.0.1:' + site.port
  const result = await crawlAndCollect(siteBase + '/')

  assert.strictEqual(result.finishedCalls, 1)
  assert.strictEqual(external.hitCount('/target'), 1, 'redirect target is fetched by fetch()')

  const reportedUrls = result.succeeded.map(function (page) { return page.url })
  assert.deepStrictEqual(reportedUrls, [siteBase + '/'])
  assert.deepStrictEqual(result.finishedUrls, [siteBase + '/'])
  assert.deepStrictEqual(hostsOf(result.succeeded), new Set(['127.0.0.1']))
})

test('all-finished fires once even when every link is filtered out', async function (t) {
  const external = await startServer({
    '/x': html('<p>x</p>')
  })
  t.after(function () { return external.close() })

  const externalBase = 'http://localhost:' + external.port
  const site = await startServer({
    '/': html(
      '<a href="' + externalBase + '/x">x</a>' +
            '<a href="https://example.invalid/page">invalid host</a>'
    )
  })
  t.after(function () { return site.close() })

  const siteBase = 'http://127.0.0.1:' + site.port
  const result = await crawlAndCollect(siteBase + '/')

  assert.strictEqual(result.finishedCalls, 1)
  assert.strictEqual(external.totalHits(), 0)
  assert.strictEqual(site.totalHits(), 1)
  assert.deepStrictEqual(result.finishedUrls, [siteBase + '/'])
  assert.deepStrictEqual(result.succeeded.map(function (page) { return page.url }), [siteBase + '/'])
})

test('whiteList and blackList still filter urls on the same host', async function (t) {
  const site = await startServer({
    '/': html('<a href="/keep">keep</a><a href="/skip">skip</a>')
  })
  t.after(function () { return site.close() })

  const siteBase = 'http://127.0.0.1:' + site.port
  const result = await crawlAndCollect(siteBase + '/', { blackList: ['/skip'] })

  assert.strictEqual(site.hitCount('/keep'), 1)
  assert.strictEqual(site.hitCount('/skip'), 0)
  assert.deepStrictEqual(result.finishedUrls.slice().sort(), [siteBase + '/', siteBase + '/keep'].sort())
  assert.strictEqual(result.finishedCalls, 1)
})

test('crawl stops at the configured depth', async function (t) {
  const site = await startServer({
    '/': html('<a href="/level1">1</a>'),
    '/level1': html('<a href="/level2">2</a>'),
    '/level2': html('<a href="/level3">3</a>')
  })
  t.after(function () { return site.close() })

  const siteBase = 'http://127.0.0.1:' + site.port
  const result = await crawlAndCollect(siteBase + '/', { depth: 2 })

  assert.strictEqual(result.finishedCalls, 1)
  assert.strictEqual(site.hitCount('/'), 1)
  assert.strictEqual(site.hitCount('/level1'), 1)
  assert.strictEqual(site.hitCount('/level2'), 0)
  assert.deepStrictEqual(
    result.finishedUrls.slice().sort(),
    [siteBase + '/', siteBase + '/level1'].sort()
  )
})

test('many cross-linking pages are each crawled once under a low concurrency limit', async function (t) {
  const routes = {}
  const links = []
  for (let i = 1; i <= 15; i++) {
    links.push('<a href="/p/' + i + '">p' + i + '</a>')
    // Every page links back to every other page: a dense duplicate graph.
    const backLinks = []
    for (let j = 1; j <= 15; j++) {
      if (j !== i) backLinks.push('<a href="/p/' + j + '">p' + j + '</a>')
    }
    routes['/p/' + i] = html(backLinks.join('') + '<a href="/">home</a><a href="/p/' + i + '">self</a>')
  }
  routes['/'] = html(links.join(''))
  const site = await startServer(routes)
  t.after(function () { return site.close() })

  const siteBase = 'http://127.0.0.1:' + site.port
  const result = await crawlAndCollect(siteBase + '/', {
    depth: 3,
    maxConcurrentRequests: 1,
    maxRequestsPerSecond: 5000
  })

  assert.strictEqual(result.finishedCalls, 1)
  assert.strictEqual(site.totalHits(), 16, 'the home page and 15 pages, one request each')
  assert.strictEqual(new Set(result.finishedUrls).size, 16)
  assert.strictEqual(result.finishedUrls.length, 16)
  assert.strictEqual(new Set(result.succeeded.map(function (page) { return page.url })).size, 16)
})

test('_isUrlInScope accepts the root host and its subdomains only', function () {
  const crawler = new Crawler()
  crawler._setRootUrl('http://example.com:8080/')

  assert.strictEqual(crawler._isUrlInScope('http://example.com/a'), true)
  assert.strictEqual(crawler._isUrlInScope('https://example.com/a'), true)
  assert.strictEqual(crawler._isUrlInScope('http://sub.example.com/a'), true)
  assert.strictEqual(crawler._isUrlInScope('http://deep.sub.example.com/a'), true)
  assert.strictEqual(crawler._isUrlInScope('http://other.com/a'), false)
  assert.strictEqual(crawler._isUrlInScope('http://notexample.com/a'), false)
  assert.strictEqual(crawler._isUrlInScope('http://example.com.evil.com/a'), false)
  assert.strictEqual(crawler._isUrlInScope('ftp://example.com/a'), false)
  assert.strictEqual(crawler._isUrlInScope('mailto:a@example.com'), false)
})
