import assert from 'node:assert/strict'
import http from 'node:http'

export async function runCrawlTests(m) {
  let passed = 0
  const hits = []
  const article = (title, links = '') => `<html><head><title>${title}</title></head><body><article><p>${title} ${'Readable article content. '.repeat(24)}</p></article>${links}</body></html>`
  const readablePaths = ['/feedback', '/admin-guide', '/story?next=/login', '/story?attachment=guide.pdf']
  const skippedPaths = ['/login', '/login/reset', '/user/signin', '/wp-login.php', '/feed', '/api/items', '/admin/users', '/sitemap.xml', '/robots.txt', '/cdn-cgi/trace', '/asset.png?v=1']
  const server = http.createServer((req, res) => {
    hits.push(req.url)
    const u = new URL(req.url, 'http://fixture.test')
    res.setHeader('content-type', 'text/html; charset=utf-8')
    if (u.pathname === '/article-one') return res.end(article('ARTICLE_ONE', '<a href="/article-two">下一篇</a>'))
    if (u.pathname === '/article-two') return res.end(article('UNRELATED_ARTICLE_TWO'))
    if (u.pathname === '/links') return res.end(article('LINKS', '<a href="/story?id=7&amp;page=2">Story</a><a href="/story?id=7&#38;page=2">Duplicate</a><a href="/raw?id=7&copy=2">Raw query</a><a href="/once?q=&amp;amp;">Once</a>'))
    if (u.pathname === '/base-links') return res.end('<html><head><base href="/base&#47;"></head><body><a href="child?x=1&amp;y=2">Child</a></body></html>')
    if (u.pathname === '/entity-one') return res.end(article('ENTITY_ONE', '<a href="/entity-two?id=7&amp;page=2">下一页</a>'))
    if (u.pathname === '/entity-two') return res.end(article(u.searchParams.get('page') === '2' ? 'CORRECT_SECOND_PAGE' : 'WRONG_QUERY_PAGE'))
    if (u.pathname === '/budget') return res.end(article('BUDGET', Array.from({ length: 5 }, (_, i) => `<a href="/missing-${i}">Missing ${i}</a>`).join('') + '<a href="/success">Success</a>'))
    if (u.pathname === '/success') return res.end(article('SUCCESS'))
    if (u.pathname === '/path-filter') return res.end(article('PATH_FILTER', [...readablePaths, ...skippedPaths].map((path, i) => `<a href="${path}">Link ${i}</a>`).join('')))
    if (readablePaths.includes(req.url)) return res.end(article('READABLE_PATH'))
    if (u.pathname === '/redirect-root') return res.end(article('ROOT', '<a href="/outside-hop">External</a><a href="/outside-meta">Meta</a>'))
    if (u.pathname === '/outside-hop') {
      res.writeHead(302, { location: `http://localhost:${server.address().port}/outside` })
      return res.end()
    }
    if (u.pathname === '/outside-meta') return res.end(`<html><head><meta http-equiv="refresh" content="0;url=http://localhost:${server.address().port}/outside"></head><body>Redirecting</body></html>`)
    if (u.pathname === '/outside') return res.end(article('FOREIGN_HOST_CONTENT'))
    if (u.pathname === '/canonical-entry') {
      res.writeHead(302, { location: `http://localhost:${server.address().port}/canonical-root` })
      return res.end()
    }
    if (u.pathname === '/canonical-root') return res.end(article('CANONICAL_ROOT', '<a href="/canonical-child">Child</a>'))
    if (u.pathname === '/canonical-child') return res.end(article('CANONICAL_CHILD', '<a href="/canonical-root#top">Root</a>'))
    if (u.pathname === '/aliases') return res.end(article('ALIASES', '<a href="/alias">Alias</a><a href="/target">Target</a>'))
    if (u.pathname === '/alias') {
      res.writeHead(302, { location: '/target' })
      return res.end()
    }
    if (u.pathname === '/target') return res.end(article('TARGET'))
    if (u.pathname === '/outcome') {
      return res.end(article('OUTCOME_ROOT',
        '<a href="/ok-page">OK</a><a href="/logo.png">Logo</a><a href="/login">Login</a>' +
        '<a href="/go-external">External</a><a href="/broken-500">Broken</a><a href="/level-1">Level 1</a>'))
    }
    if (u.pathname === '/ok-page') return res.end(article('OK_PAGE_BODY'))
    if (u.pathname === '/go-external') {
      res.writeHead(302, { location: `http://localhost:${server.address().port}/foreign-land` })
      return res.end()
    }
    if (u.pathname === '/foreign-land') return res.end(article('FOREIGN_LAND'))
    if (u.pathname === '/broken-500') {
      res.writeHead(500)
      return res.end('Server error')
    }
    if (u.pathname === '/level-1') return res.end(article('LEVEL_ONE_BODY', '<a href="/level-2">Level 2</a><a href="/ok-page">OK again</a>'))
    if (u.pathname === '/level-2') return res.end(article('LEVEL_TWO_BODY'))
    if (u.pathname === '/wide') {
      return res.end(article('WIDE', Array.from({ length: 45 }, (_, i) => `<a href="/w-${i}">W${i}</a>`).join('')))
    }
    if (u.pathname.startsWith('/w-')) return res.end(article('W_PAGE'))
    res.writeHead(404)
    res.end('Not found')
  })
  await new Promise((resolve) => server.listen(0, resolve))
  const base = `http://127.0.0.1:${server.address().port}`
  const registered = {}
  m.apply({ tools: { register: (tool) => { registered[tool.name] = tool } }, effect: () => {}, get: () => undefined }, { spaRender: false })
  try {
    assert.equal(m.findNextLink('<a href="/next">下一篇</a>', base), null)
    assert.equal(m.findNextLink('<a href="/next">下一页 ›</a>', base), `${base}/next`)
    passed++

    const articleResult = await registered.read_url.execute({ url: `${base}/article-one`, maxChars: 20000 })
    assert.ok(articleResult.text.includes('ARTICLE_ONE'))
    assert.ok(!articleResult.text.includes('UNRELATED_ARTICLE_TWO'))
    assert.equal(articleResult.paginated, undefined)
    passed++

    const linksResult = await registered.read_url_links.execute({ url: `${base}/links` })
    assert.equal(linksResult.count, 3)
    assert.equal(linksResult.links[0].url, `${base}/story?id=7&page=2`)
    passed++
    assert.equal(linksResult.links[1].url, `${base}/raw?id=7&copy=2`)
    assert.equal(linksResult.links[2].url, `${base}/once?q=&amp;`)
    passed++

    const baseResult = await registered.read_url_links.execute({ url: `${base}/base-links` })
    assert.equal(baseResult.links[0].url, `${base}/base/child?x=1&y=2`)
    passed++

    assert.equal(m.findNextLink('<link rel="next" href="/next?id=7&amp;page=2">', base), `${base}/next?id=7&page=2`)
    assert.equal(m.findNextLink('<a href="/next?id=7&#x26;page=2">Next</a>', base), `${base}/next?id=7&page=2`)
    passed++

    const paginated = await registered.read_url.execute({ url: `${base}/entity-one`, maxChars: 20000 })
    assert.equal(paginated.paginated, 2)
    assert.ok(paginated.text.includes('CORRECT_SECOND_PAGE'))
    assert.ok(!paginated.text.includes('WRONG_QUERY_PAGE'))
    passed++

    hits.length = 0
    const budget = await registered.read_url_site.execute({ url: `${base}/budget`, maxPages: 2, maxDepth: 1 })
    assert.equal(budget.total, 2)
    assert.equal(budget.succeeded, 1)
    assert.equal(budget.failed, 1)
    assert.deepEqual(hits, ['/budget', '/missing-0'])
    passed++

    hits.length = 0
    const fractional = await registered.read_url_site.execute({ url: `${base}/budget`, maxPages: 2.9, maxDepth: 1 })
    assert.equal(fractional.total, 2)
    assert.equal(hits.length, 2)
    passed++

    hits.length = 0
    const filtered = await registered.read_url_site.execute({ url: `${base}/path-filter`, maxPages: 20, maxDepth: 1 })
    assert.equal(filtered.failed, 0)
    assert.equal(filtered.succeeded, readablePaths.length + 1)
    // hits records server-side arrival order, which is nondeterministic for
    // the concurrently fetched depth-1 batch — assert entry-first plus the
    // exact set of followed paths instead of a strict arrival sequence.
    assert.equal(hits[0], '/path-filter')
    assert.deepEqual([...hits.slice(1)].sort(), [...readablePaths].sort())
    passed++

    const redirects = await registered.read_url_site.execute({ url: `${base}/redirect-root`, maxPages: 5, maxDepth: 1, includeContent: true })
    assert.equal(redirects.host, '127.0.0.1')
    assert.equal(redirects.succeeded, 1)
    assert.equal(redirects.failed, 2)
    assert.ok(redirects.failures.every((failure) => failure.error.includes('outside the site')))
    assert.ok(!JSON.stringify(redirects.pages).includes('FOREIGN_HOST_CONTENT'))
    passed++

    const canonical = await registered.read_url_site.execute({ url: `${base}/canonical-entry`, maxPages: 5, maxDepth: 2 })
    assert.equal(canonical.host, 'localhost')
    assert.deepEqual(canonical.pages.map((page) => new URL(page.url).pathname), ['/canonical-root', '/canonical-child'])
    passed++

    const aliases = await registered.read_url_site.execute({ url: `${base}/aliases`, maxPages: 5, maxDepth: 1 })
    assert.equal(aliases.succeeded, 2)
    assert.deepEqual(aliases.pages.map((page) => new URL(page.url).pathname), ['/aliases', '/target'])
    passed++

    // Per-reason crawl outcomes: success pages keep their content and BFS
    // order while every skipped link and failed page is itemized, and the
    // summary tallies match the item counts exactly.
    const outcome = await registered.read_url_site.execute({ url: `${base}/outcome`, maxPages: 20, maxDepth: 1, includeContent: true })
    assert.deepEqual(outcome.pages.map((page) => new URL(page.url).pathname), ['/outcome', '/ok-page', '/level-1'])
    assert.ok(outcome.pages[1].text.includes('OK_PAGE_BODY'))
    assert.ok(outcome.pages[2].text.includes('LEVEL_ONE_BODY'))
    assert.equal(outcome.failed, 2)
    const failByPath = new Map(outcome.failures.map((f) => [new URL(f.url).pathname, f]))
    assert.equal(failByPath.get('/go-external').reason, 'redirect-offsite')
    assert.equal(failByPath.get('/broken-500').reason, 'fetch-error')
    const skipByPath = new Map(outcome.skipped.map((s) => [new URL(s.url).pathname, s.reason]))
    assert.equal(skipByPath.get('/logo.png'), 'noise')
    assert.equal(skipByPath.get('/login'), 'noise')
    assert.equal(skipByPath.get('/level-2'), 'depth-limit')
    assert.equal(skipByPath.get('/ok-page'), 'duplicate')
    assert.equal(outcome.skipped.length, 4)
    const tally = (o) => Object.values(o).reduce((x, y) => x + y, 0)
    assert.equal(tally(outcome.summary.skipped), outcome.skipped.length)
    assert.equal(tally(outcome.summary.failed), outcome.failures.length)
    assert.equal(outcome.summary.skipped.noise, 2)
    assert.equal(outcome.summary.skipped['depth-limit'], 1)
    assert.equal(outcome.summary.skipped.duplicate, 1)
    assert.equal(outcome.summary.failed['redirect-offsite'], 1)
    assert.equal(outcome.summary.failed['fetch-error'], 1)
    assert.ok(!JSON.stringify(outcome).includes('FOREIGN_LAND'))
    assert.ok(!JSON.stringify(outcome).includes('LEVEL_TWO_BODY'))
    const outcomeText = registered.read_url_site.output.render({}, outcome)[0].text
    assert.ok(outcomeText.includes('[跳过]') && outcomeText.includes('深度超限'))
    assert.ok(outcomeText.includes('重定向跳出站点'))
    passed++
    console.log('  ok - crawl itemizes skip/failure reasons with matching tallies')

    // Queue cap: links beyond maxPages*20 are skipped with queue-limit.
    const wide = await registered.read_url_site.execute({ url: `${base}/wide`, maxPages: 2, maxDepth: 1 })
    assert.equal(wide.succeeded + wide.failed, 2)
    assert.equal(wide.skipped.filter((s) => s.reason === 'queue-limit').length, 5)
    assert.equal(wide.summary.skipped['queue-limit'], 5)
    passed++
    console.log('  ok - queue cap overflow recorded as queue-limit skips')

    const seamTools = {}
    const seamCalls = []
    const seam = { fetch: async ({ url }) => {
      seamCalls.push(url)
      const path = new URL(url).pathname
      const content = path === '/seam-shell'
        ? '<html><head><meta http-equiv="refresh" content="0;url=/seam-target"></head><body>Redirecting</body></html>'
        : path === '/seam-root'
          ? article('SEAM_ROOT', '<a href="/seam-shell">Shell</a>')
          : article('SEAM_TARGET', '<a href="/seam-leaf">Leaf</a>')
      return { statusCode: 200, body: { kind: 'html', content }, url }
    } }
    m.apply({ tools: { register: (tool) => { seamTools[tool.name] = tool } }, effect: () => {}, get: () => seam }, { spaRender: false })
    hits.length = 0
    const seamLinks = await seamTools.read_url_links.execute({ url: `${base}/seam-shell` })
    assert.equal(seamLinks.links[0].url, `${base}/seam-leaf`)
    assert.deepEqual(seamCalls.map((url) => new URL(url).pathname), ['/seam-shell', '/seam-target'])
    assert.equal(hits.length, 0)
    passed++

    seamCalls.length = 0
    const seamSite = await seamTools.read_url_site.execute({ url: `${base}/seam-root`, maxPages: 2, maxDepth: 1 })
    assert.deepEqual(seamSite.pages.map((page) => new URL(page.url).pathname), ['/seam-root', '/seam-target'])
    assert.deepEqual(seamCalls.map((url) => new URL(url).pathname), ['/seam-root', '/seam-shell', '/seam-target'])
    assert.equal(hits.length, 0)
    passed++

    const refusedTools = {}
    m.apply({ tools: { register: (tool) => { refusedTools[tool.name] = tool } }, effect: () => {}, get: () => ({ fetch: async () => { throw new Error('Provider refused') } }) }, { spaRender: false })
    const refusedLinks = await refusedTools.read_url_links.execute({ url: `${base}/success` })
    const refusedSite = await refusedTools.read_url_site.execute({ url: `${base}/success` })
    assert.ok(refusedLinks.error.includes('Provider refused'))
    assert.equal(refusedSite.succeeded, 0)
    assert.equal(refusedSite.failed, 1)
    assert.ok(refusedSite.failures[0].error.includes('Provider refused'))
    assert.equal(hits.length, 0)
    passed++

    const summaryTools = {}
    const summaryBody = 'Summary fact ' + 'q'.repeat(420) + ' END_MARKER'
    m.apply({ tools: { register: tool => { summaryTools[tool.name] = tool } }, effect: () => {},
      get: () => ({ fetch: async ({ url }) => ({ url, statusCode: 200,
        body: { kind: 'html', content: `<article><p>${summaryBody}</p></article>` } }) }),
    }, { spaRender: false })
    const summaryTool = summaryTools.read_url_site
    const summaryArgs = { url: 'https://crawl-summary.invalid/', includeContent: true, maxCharsPerPage: 500 }
    const summary = await summaryTool.execute(summaryArgs)
    const summaryText = summaryTool.output.render(summaryArgs, summary)[0].text
    assert.equal(summary.pages[0].text, summaryBody)
    assert.ok(summaryText.includes(summaryBody))
    passed++
    console.log('  ok - site renderer preserves the requested bounded summary')

    const shortArgs = { ...summaryArgs, maxCharsPerPage: 200 }
    const shortSummary = await summaryTool.execute(shortArgs)
    const shortText = summaryTool.output.render(shortArgs, shortSummary)[0].text
    assert.equal(shortSummary.pages[0].text.length, 200)
    assert.ok(shortText.includes(shortSummary.pages[0].text))
    assert.ok(!shortText.includes('END_MARKER'))
    passed++
    console.log('  ok - site summary rendering respects the character limit')

    const structureArgs = { url: summaryArgs.url }
    const structure = await summaryTool.execute(structureArgs)
    assert.equal(structure.pages[0].text, undefined)
    assert.ok(!summaryTool.output.render(structureArgs, structure)[0].text.includes('Summary fact'))
    passed++
    console.log('  ok - site content remains omitted by default')
  } finally {
    server.closeAllConnections()
    await new Promise((resolve) => server.close(resolve))
  }
  return passed
}
