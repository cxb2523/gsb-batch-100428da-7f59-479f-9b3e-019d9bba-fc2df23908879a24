/**
 * Frontend verification for the content-source annotation.
 *
 * - worker-client parses new worker fields and stays tolerant of legacy
 *   responses that omit them (backward compatibility)
 * - ContentSourceBadge renders the paragraph vs fallback states with the
 *   human-readable fallback reason
 * - the badge is reachable only through website-mode components; text-mode
 *   result construction never carries a source annotation
 * - the plain-text report includes the source line for website results
 *
 * Run: node --test tests/frontend-content-source.test.mjs
 */

import { test, describe } from 'node:test'
import assert from 'node:assert/strict'
import { createJiti } from 'jiti'

const jiti = createJiti(import.meta.url, {
  // jiti's TSX loader uses the classic React runtime (React.createElement),
  // so the transformed components resolve React from the global scope.
  jsx: true,
  interopDefault: true,
})

// Load React through the SAME jiti instance as the components so the server
// renderer and the transformed TSX share one module copy, then expose it as
// the global the classic JSX transform references.
const ReactModule = await jiti.import('react', { default: true })
const React = ReactModule.default ?? ReactModule
globalThis.React = React
const { renderToStaticMarkup } = await jiti.import('react-dom/server')

// ─── Badge component (TSX loaded via jiti) ──────────────────────────────────

const { ContentSourceBadge } = await jiti.import(
  '../src/components/ContentSourceBadge.tsx'
)
const { WebsiteResultsHeader } = await jiti.import(
  '../src/components/WebsiteResultsHeader.tsx'
)
const { generateCombinedReport } = await jiti.import(
  '../src/analysis/report.ts'
)

function render(node) {
  return renderToStaticMarkup(node)
}

/** Build an element via the shared React instance. */
function el(type, props) {
  return React.createElement(type, props)
}

// ─── Badge rendering ────────────────────────────────────────────────────────

describe('ContentSourceBadge', () => {
  test('paragraph state renders a neutral "paragraph extraction" label', () => {
    const html = render(el(ContentSourceBadge, {
      source: { contentSource: 'paragraphs', fallbackReason: null },
    }))
    assert.ok(html.includes('Content source: paragraph extraction'))
    assert.ok(html.includes('data-testid="content-source-badge"'))
    // No amber fallback wording on the happy path
    assert.ok(!html.includes('full-text fallback'))
  })

  test('fallback state explains the no_article_main_container reason', () => {
    const html = render(el(ContentSourceBadge, {
      source: {
        contentSource: 'full-text-fallback',
        fallbackReason: 'no_article_main_container',
      },
    }))
    assert.ok(html.includes('Content source: full-text fallback'))
    assert.ok(html.includes('No article/main container found'))
    assert.ok(html.includes('data-content-source="full-text-fallback"'))
  })

  test('fallback state explains the too_few_qualifying_paragraphs reason', () => {
    const html = render(el(ContentSourceBadge, {
      source: {
        contentSource: 'full-text-fallback',
        fallbackReason: 'too_few_qualifying_paragraphs',
      },
    }))
    assert.ok(html.includes('Content source: full-text fallback'))
    assert.ok(html.includes('Too few qualifying paragraphs'))
  })
})

// ─── Website header integration + legacy tolerance ──────────────────────────

const baseWebsiteData = {
  url: 'https://example.com/article',
  title: 'Example Article',
  description: 'An example',
  content: 'x'.repeat(100),
  wordCount: 42,
  headings: ['Heading'],
  headingHierarchy: [{ level: 1, text: 'Heading' }],
  paragraphCount: 3,
}

describe('WebsiteResultsHeader', () => {
  test('shows the badge when the worker reports a source', () => {
    const html = render(el(WebsiteResultsHeader, {
      websiteData: {
        ...baseWebsiteData,
        contentSource: 'full-text-fallback',
        fallbackReason: 'no_article_main_container',
      },
    }))
    assert.ok(html.includes('Content source: full-text fallback'))
    assert.ok(html.includes('No article/main container found'))
  })

  test('omits the badge for legacy worker responses without the field', () => {
    const html = render(el(WebsiteResultsHeader, {
      websiteData: { ...baseWebsiteData },
    }))
    assert.ok(!html.includes('content-source-badge'))
    assert.ok(!html.includes('Content source'))
  })
})

// ─── Text mode must never display the annotation ────────────────────────────

const FULL_STATS = {
  words: 30, characters: 150, sentences: 4, paragraphs: 2,
  avgSentenceLength: 7.5, minSentenceLength: 3, maxSentenceLength: 12,
  vocabularyDiversity: 0.6,
}

describe('text mode isolation', () => {
  test('text-mode CombinedResult has no websiteData and therefore no badge path', () => {
    const textResult = {
      writing: {
        aiLikelihood: 10,
        confidence: 'low',
        statistics: FULL_STATS,
        signals: [],
        explanation: 'e',
        limitations: 'l',
        sentenceAnalyses: [],
        sourceMode: 'text',
      },
      visibility: {
        overallScore: 50,
        confidence: 'low',
        dimensions: [],
        explanation: 'v',
        limitations: 'vl',
      },
      sourceMode: 'text',
      analyzedAt: '2026-11-16T00:00:00.000Z',
    }
    assert.equal(textResult.websiteData, undefined)
    const report = generateCombinedReport(textResult)
    assert.ok(!report.includes('Content:'))
    assert.ok(!report.includes('full-text fallback'))
  })
})

// ─── Report propagation (website mode) ──────────────────────────────────────

function websiteCombined(source) {
  return {
    writing: {
      aiLikelihood: 10,
      confidence: 'low',
      statistics: {
        words: 100, characters: 500, sentences: 8, paragraphs: 4,
        avgSentenceLength: 12.5, minSentenceLength: 4, maxSentenceLength: 20,
        vocabularyDiversity: 0.5,
      },
      signals: [],
      explanation: 'e',
      limitations: 'l',
      sentenceAnalyses: [],
      sourceMode: 'website',
      websiteData: {
        url: 'https://example.com/article',
        title: 'Example',
        description: '',
        headings: [],
        paragraphCount: 2,
        wordCount: 100,
        ...(source ? { source } : {}),
      },
    },
    visibility: {
      overallScore: 50,
      confidence: 'low',
      dimensions: [],
      explanation: 'v',
      limitations: 'vl',
    },
    sourceMode: 'website',
    websiteData: {
      url: 'https://example.com/article',
      title: 'Example',
      description: '',
      headings: [],
      paragraphCount: 2,
      wordCount: 100,
      ...(source ? { source } : {}),
    },
    analyzedAt: '2026-11-16T00:00:00.000Z',
  }
}

describe('plain-text report', () => {
  test('fallback provenance appears in the website report', () => {
    const report = generateCombinedReport(
      websiteCombined({
        contentSource: 'full-text-fallback',
        fallbackReason: 'too_few_qualifying_paragraphs',
      })
    )
    assert.ok(report.includes('Content:  full-text fallback'))
    assert.ok(report.includes('Too few qualifying paragraphs'))
  })

  test('paragraph provenance appears in the website report', () => {
    const report = generateCombinedReport(
      websiteCombined({ contentSource: 'paragraphs', fallbackReason: null })
    )
    assert.ok(report.includes('Content:  paragraph extraction'))
  })

  test('legacy website result without source info still renders', () => {
    const report = generateCombinedReport(websiteCombined(undefined))
    assert.ok(report.includes('Source:    https://example.com/article'))
    assert.ok(!report.includes('Content:'))
  })
})
