/**
 * worker-client verification for content-source pass-through.
 *
 * Confirms the typed fetch wrapper:
 *   - relays contentSource / fallbackReason from a modern worker response
 *   - tolerates legacy worker responses that omit the new fields
 *     (backward compatibility — old field semantics unchanged)
 *   - maps INSUFFICIENT_CONTENT (the post-fallback failure) to a user error
 *
 * Run: node --test tests/worker-client.test.mjs
 */

import { test, describe, afterEach } from 'node:test'
import assert from 'node:assert/strict'
import { createJiti } from 'jiti'

// Vite injects import.meta.env.VITE_WORKER_URL at build time. Under the
// Node/jiti runtime that access becomes process.env.VITE_WORKER_URL, so set
// it before importing the client — the client then targets the mock endpoint.
process.env.VITE_WORKER_URL = 'https://worker.test/extract'

const jiti = createJiti(import.meta.url)

const { fetchWebsiteContent } = await jiti.import('../src/api/worker-client.ts')

const TARGET = 'https://example.com/article'

function setWorkerResponse(payload, ok = true) {
  globalThis.fetch = async (input) => {
    // Assert the client still calls the worker with an encoded ?url= param
    const called = String(input)
    if (!called.startsWith('https://worker.test/extract?url=')) {
      throw new Error(`unexpected worker URL: ${called}`)
    }
    return new Response(JSON.stringify(payload), {
      status: ok ? 200 : 422,
      headers: { 'content-type': 'application/json' },
    })
  }
}

afterEach(() => {
  delete globalThis.fetch
})

describe('fetchWebsiteContent source pass-through', () => {
  test('relays paragraph extraction provenance', async () => {
    setWorkerResponse({
      url: TARGET,
      title: 'T',
      description: 'D',
      content: 'word '.repeat(30).trim(),
      wordCount: 30,
      headings: [],
      headingHierarchy: [],
      paragraphCount: 2,
      contentSource: 'paragraphs',
      fallbackReason: null,
    })

    const result = await fetchWebsiteContent(TARGET)
    assert.equal(result.ok, true)
    if (!result.ok) throw new Error('expected ok result')
    assert.equal(result.data.contentSource, 'paragraphs')
    assert.equal(result.data.fallbackReason, null)
    // Legacy fields untouched
    assert.equal(result.data.wordCount, 30)
    assert.equal(result.data.paragraphCount, 2)
  })

  test('relays full-text fallback provenance and reason', async () => {
    setWorkerResponse({
      url: TARGET,
      title: 'T',
      description: '',
      content: 'word '.repeat(25).trim(),
      wordCount: 25,
      headings: [],
      headingHierarchy: [],
      paragraphCount: 0,
      contentSource: 'full-text-fallback',
      fallbackReason: 'no_article_main_container',
    })

    const result = await fetchWebsiteContent(TARGET)
    assert.equal(result.ok, true)
    if (!result.ok) throw new Error('expected ok result')
    assert.equal(result.data.contentSource, 'full-text-fallback')
    assert.equal(result.data.fallbackReason, 'no_article_main_container')
  })

  test('legacy response without new fields still resolves (backward compatible)', async () => {
    setWorkerResponse({
      url: TARGET,
      title: 'Legacy',
      description: '',
      content: 'word '.repeat(25).trim(),
      wordCount: 25,
      headings: [],
      headingHierarchy: [],
      paragraphCount: 1,
    })

    const result = await fetchWebsiteContent(TARGET)
    assert.equal(result.ok, true)
    if (!result.ok) throw new Error('expected ok result')
    assert.equal(result.data.contentSource, undefined)
    assert.equal(result.data.fallbackReason, undefined)
    assert.equal(result.data.title, 'Legacy')
  })

  test('INSUFFICIENT_CONTENT after failed fallback becomes a user-facing error', async () => {
    setWorkerResponse(
      { error: 'Not enough readable content found.', code: 'INSUFFICIENT_CONTENT' },
      false
    )

    const result = await fetchWebsiteContent(TARGET)
    assert.equal(result.ok, false)
    if (result.ok) throw new Error('expected error result')
    assert.ok(result.headline.length > 0)
    assert.equal(result.retryable, false)
    // No technical code leaks into user-facing strings
    assert.ok(!result.headline.includes('INSUFFICIENT_CONTENT'))
    assert.ok(!result.detail.includes('INSUFFICIENT_CONTENT'))
  })
})
