/**
 * ContentSourceBadge
 * Shows where the analyzed website body text came from:
 * - paragraph extraction (preferred path), or
 * - full visible-text fallback (with the reason it was triggered).
 *
 * Only rendered for website analyses — text analysis mode never shows it.
 */

import type { ContentSourceInfo, FallbackReason } from '../types'

const FALLBACK_REASON_TEXT: Record<FallbackReason, string> = {
  no_article_main_container:
    'No article/main container found — analysis uses all visible page text.',
  too_few_qualifying_paragraphs:
    'Too few qualifying paragraphs — analysis uses all visible page text.',
}

interface ContentSourceBadgeProps {
  source: ContentSourceInfo
}

export function ContentSourceBadge({ source }: ContentSourceBadgeProps) {
  // Preferred path — quiet, neutral annotation.
  if (source.contentSource === 'paragraphs') {
    return (
      <div
        className="flex items-start gap-2 rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800/60 px-3 py-2"
        data-testid="content-source-badge"
      >
        <svg viewBox="0 0 16 16" fill="currentColor" className="w-3.5 h-3.5 mt-0.5 text-slate-400 dark:text-slate-500 flex-shrink-0" aria-hidden="true">
          <path d="M8 1a7 7 0 100 14A7 7 0 008 1zm3.7 5.2l-4.2 4.2a1 1 0 01-1.4 0L4.3 8.6a1 1 0 111.4-1.4l1.1 1.1 3.5-3.5a1 1 0 111.4 1.4z"/>
        </svg>
        <div className="flex flex-col">
          <span className="text-[11px] font-semibold text-slate-600 dark:text-slate-300">
            Content source: paragraph extraction
          </span>
          <span className="text-[11px] text-slate-400 dark:text-slate-500">
            Main text extracted from the page&apos;s paragraphs.
          </span>
        </div>
      </div>
    )
  }

  // Fallback path — amber warning so users know the body text differs
  // from the preferred article extraction.
  const reasonText = source.fallbackReason
    ? FALLBACK_REASON_TEXT[source.fallbackReason]
    : 'Analysis uses all visible page text.'

  return (
    <div
      role="note"
      className="flex items-start gap-2 rounded-lg border border-amber-200 dark:border-amber-500/40 bg-amber-50 dark:bg-amber-500/10 px-3 py-2"
      data-testid="content-source-badge"
      data-content-source="full-text-fallback"
    >
      <svg viewBox="0 0 16 16" fill="currentColor" className="w-3.5 h-3.5 mt-0.5 text-amber-500 dark:text-amber-400 flex-shrink-0" aria-hidden="true">
        <path d="M8 1.5a1 1 0 01.87.5l6.4 11a1 1 0 01-.87 1.5H1.6a1 1 0 01-.87-1.5l6.4-11A1 1 0 018 1.5zM8 5a.9.9 0 00-.9.9v3.2a.9.9 0 101.8 0V5.9A.9.9 0 008 5zm0 6.4a1.1 1.1 0 100 2.2 1.1 1.1 0 000-2.2z"/>
      </svg>
      <div className="flex flex-col">
        <span className="text-[11px] font-semibold text-amber-700 dark:text-amber-300">
          Content source: full-text fallback
        </span>
        <span className="text-[11px] text-amber-600 dark:text-amber-400/90 leading-relaxed">
          {reasonText}
        </span>
      </div>
    </div>
  )
}
