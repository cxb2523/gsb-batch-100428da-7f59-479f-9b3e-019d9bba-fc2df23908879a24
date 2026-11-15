/**
 * Worker verification for the content-source annotation feature.
 *
 * Covers the three required scenarios:
 *   1. sufficient paragraphs  → contentSource 'paragraphs'
 *   2. fallback triggered     → contentSource 'full-text-fallback' + reason
 *   3. fallback still <20 words → INSUFFICIENT_CONTENT (422)
 *
 * Plus regressions for the hard constraints that must not be bypassed:
 *   - 10,000-character cap on both extraction paths
 *   - 20-word minimum gate after fallback
 *   - HTTPS / SSRF / 2MB / content-type checks remain enforced
 *   - response stays backward compatible (all legacy fields unchanged)
 *
 * Run: node --test tests/worker-content-source.test.mjs
 */

import { test, describe, mock } from 'node:test'
import assert from 'node:assert/strict'
import { createJiti } from 'jiti'

const jiti = createJiti(import.meta.url)
const workerMod = await jiti.import('../worker/index.ts')
const worker = workerMod.default

const WORKER_URL = 'https://miantrace.test/extract'

/** Build a request to the worker for a target URL. */
function makeRequest(targetUrl, init = {}) {
  const url = targetUrl
    ? `${WORKER_URL}?url=${encodeURIComponent(targetUrl)}`
    : WORKER_URL
  return new Request(url, { method: 'GET', ...init })
}

/** Response whose `url` reflects the (mock) final request URL. */
class MockResponse extends Response {
  #mockUrl
  constructor(body, init, url) {
    super(body, init)
    this.#mockUrl = url
  }
  get url() { return this.#mockUrl }
}

/** Run the worker with a mocked fetch returning the given HTML. */
async function runWithHtml(html, { contentType = 'text/html; charset=utf-8', finalUrl } = {}) {
  const originalFetch = globalThis.fetch
  const resolvedUrl = finalUrl ?? 'https://example.com/page'
  globalThis.fetch = mock.fn(async () =>
    new MockResponse(html, {
      status: 200,
      headers: {
        'content-type': contentType,
        'content-length': String(new TextEncoder().encode(html).byteLength),
      },
    }, resolvedUrl)
  )
  try {
    return await worker.fetch(makeRequest(resolvedUrl))
  } finally {
    globalThis.fetch = originalFetch
  }
}

async function json(response) {
  return { status: response.status, body: await response.json() }
}

/** Long paragraph (>40 chars) with a recognizable repeated token word. */
function longParagraph(n) {
  return `<p>This is the ${n}th substantive paragraph containing plenty of readable words for extraction purposes here.</p>`
}

// ─── Scenario 1: paragraph extraction is sufficient ─────────────────────────

describe('scenario 1 — sufficient paragraphs', () => {
  test('returns contentSource=paragraphs and no fallback reason', async () => {
    const paragraphs = Array.from({ length: 6 }, (_, i) => longParagraph(i + 1)).join('\n')
    const html = `<!doctype html><html><head><title>Good Article</title></head>
      <body><article><h1>Heading One</h1>${paragraphs}</article></body></html>`

    const response = await runWithHtml(html)
    const { status, body } = await json(response)

    assert.equal(status, 200)
    assert.equal(body.contentSource, 'paragraphs')
    assert.equal(body.fallbackReason, null)
    assert.ok(body.wordCount >= 20, 'should clear the 20-word gate')
    assert.ok(body.content.includes('substantive paragraph'))
  })

  test('legacy fields keep their original semantics', async () => {
    const paragraphs = Array.from({ length: 4 }, (_, i) => longParagraph(i + 1)).join('\n')
    const html = `<html><head>
      <meta name="description" content="A test description">
      </head><body><main>${paragraphs}</main></body></html>`

    const response = await runWithHtml(html)
    const { body } = await json(response)

    // Every pre-existing field is still present with expected meaning
    assert.equal(typeof body.url, 'string')
    assert.equal(typeof body.title, 'string')
    assert.equal(typeof body.description, 'string')
    assert.equal(typeof body.content, 'string')
    assert.equal(typeof body.wordCount, 'number')
    assert.ok(Array.isArray(body.headings))
    assert.ok(Array.isArray(body.headingHierarchy))
    assert.equal(typeof body.paragraphCount, 'number')
    assert.ok(body.paragraphCount >= 4)
    assert.equal(body.wordCount,
      body.content.trim().split(/\s+/).filter(Boolean).length)
    assert.equal(body.description, 'A test description')
  })
})

// ─── Scenario 2: fallback is triggered ──────────────────────────────────────

describe('scenario 2 — full-text fallback', () => {
  test('no article/main container → fallback reason no_article_main_container', async () => {
    // No <article>/<main>, no qualifying <p> — only divs with enough text
    const divs = Array.from({ length: 10 }, (_, i) =>
      `<div>Visible body text number ${i} with enough words to remain after cleaning the page markup.</div>`
    ).join('')
    const html = `<html><body>${divs}</body></html>`

    const response = await runWithHtml(html)
    const { status, body } = await json(response)

    assert.equal(status, 200)
    assert.equal(body.contentSource, 'full-text-fallback')
    assert.equal(body.fallbackReason, 'no_article_main_container')
    assert.ok(body.wordCount >= 20)
    assert.ok(body.content.includes('Visible body text'))
  })

  test('article exists but paragraphs too short → too_few_qualifying_paragraphs', async () => {
    // Article container present, but <p> texts are all under 40 chars
    const shortParas = Array.from({ length: 8 }, (_, i) =>
      `<p>Short note ${i}.</p>`
    ).join('')
    const articleText = Array.from({ length: 12 }, (_, i) =>
      `<span>Article span text ${i} carries enough readable words for the fallback engine to build a body.</span> `
    ).join('')
    const html = `<html><body><article>${shortParas}${articleText}</article></body></html>`

    const response = await runWithHtml(html)
    const { status, body } = await json(response)

    assert.equal(status, 200)
    assert.equal(body.contentSource, 'full-text-fallback')
    assert.equal(body.fallbackReason, 'too_few_qualifying_paragraphs')
    assert.ok(body.wordCount >= 20)
    // Fallback content is the cleaned visible text, including span content
    assert.ok(body.content.includes('Article span text'))
  })

  test('fallback still obeys the 10,000-character cap', async () => {
    // No containers/paragraphs → forces fallback with a very large body
    const huge = '<div>' + 'visibleword '.repeat(5000) + '</div>'
    const html = `<html><body>${huge}</body></html>`

    const response = await runWithHtml(html)
    const { status, body } = await json(response)

    assert.equal(status, 200)
    assert.equal(body.contentSource, 'full-text-fallback')
    assert.ok(body.content.length <= 10000,
      `content length ${body.content.length} exceeds 10000`)
  })

  test('paragraph path also obeys the 10,000-character cap', async () => {
    const paragraphs = Array.from({ length: 200 }, (_, i) => longParagraph(i + 1)).join('')
    const html = `<html><body><article>${paragraphs}</article></body></html>`

    const response = await runWithHtml(html)
    const { body } = await json(response)

    assert.equal(body.contentSource, 'paragraphs')
    assert.ok(body.content.length <= 10000)
  })
})

// ─── Scenario 3: fallback content still insufficient ────────────────────────

describe('scenario 3 — fallback still under 20 words', () => {
  test('returns INSUFFICIENT_CONTENT with 422 even after fallback', async () => {
    // No article/main, no paragraphs, and barely any visible text
    const html = `<html><body><div>tiny text</div></body></html>`

    const response = await runWithHtml(html)
    const { status, body } = await json(response)

    assert.equal(status, 422)
    assert.equal(body.code, 'INSUFFICIENT_CONTENT')
    assert.ok(typeof body.error === 'string' && body.error.length > 0)
    // Error response must not masquerade as a success payload
    assert.equal(body.content, undefined)
    assert.equal(body.contentSource, undefined)
  })

  test('short paragraph path that still fails the gate returns 422', async () => {
    // A handful of short paragraphs (<40 chars each) → fallback → still short
    const html = `<html><body><article><p>one two</p><p>three four</p></article></body></html>`

    const response = await runWithHtml(html)
    const { status, body } = await json(response)

    assert.equal(status, 422)
    assert.equal(body.code, 'INSUFFICIENT_CONTENT')
  })
})

// ─── Security regressions: fallback must not bypass any guard ───────────────

describe('security guards remain enforced', () => {
  test('non-HTTPS URL rejected before any fetch', async () => {
    globalThis.fetch = mock.fn(async () => new Response('x'))
    try {
      const response = await worker.fetch(makeRequest('http://example.com/page'))
      const { status, body } = await json(response)
      assert.equal(status, 400)
      assert.equal(body.code, 'INVALID_URL')
      assert.equal(globalThis.fetch.mock.callCount(), 0)
    } finally {
      // fetch was replaced inside the test; restore by reassigning
      delete globalThis.fetch
    }
  })

  test('SSRF private IP rejected', async () => {
    globalThis.fetch = mock.fn(async () => new Response('x'))
    try {
      const response = await worker.fetch(makeRequest('https://192.168.1.10/admin'))
      const { status, body } = await json(response)
      assert.equal(status, 400)
      assert.equal(body.code, 'UNSAFE_URL')
      assert.equal(globalThis.fetch.mock.callCount(), 0)
    } finally {
      delete globalThis.fetch
    }
  })

  test('SSRF link-local metadata IP rejected', async () => {
    globalThis.fetch = mock.fn(async () => new Response('x'))
    try {
      const response = await worker.fetch(makeRequest('https://169.254.169.254/latest/meta-data'))
      const { status, body } = await json(response)
      assert.equal(status, 400)
      assert.equal(body.code, 'UNSAFE_URL')
      assert.equal(globalThis.fetch.mock.callCount(), 0)
    } finally {
      delete globalThis.fetch
    }
  })

  test('non-HTML content type rejected', async () => {
    const response = await runWithHtml('binary-bytes', { contentType: 'application/pdf' })
    const { status, body } = await json(response)
    assert.equal(status, 400)
    assert.equal(body.code, 'UNSUPPORTED_CONTENT_TYPE')
  })

  test('body over 2MB rejected even when extraction would need fallback', async () => {
    const originalFetch = globalThis.fetch
    const oversized = '<div>' + 'a '.repeat(1024 * 1024 + 10) + '</div>'
    globalThis.fetch = mock.fn(async () =>
      new MockResponse(oversized, {
        status: 200,
        headers: { 'content-type': 'text/html' }, // no content-length → body guard path
      }, 'https://example.com/huge')
    )
    try {
      const response = await worker.fetch(makeRequest('https://example.com/huge'))
      const { status, body } = await json(response)
      assert.equal(status, 400)
      assert.equal(body.code, 'RESPONSE_TOO_LARGE')
    } finally {
      globalThis.fetch = originalFetch
    }
  })
})
