// MianTrace — shared TypeScript types
// These will be expanded in Phase 3–5

export type ConfidenceLevel = 'low' | 'moderate' | 'high';
export type SignalLevel = 'low' | 'medium' | 'high';
export type AnalyzerTab = 'text' | 'website';

/**
 * Where the analyzed website body text came from.
 * - 'paragraphs'         : extracted from qualifying paragraph elements
 * - 'full-text-fallback' : paragraph extraction was insufficient, so the
 *                          worker fell back to cleaned visible page text
 */
export type ContentSource = 'paragraphs' | 'full-text-fallback';

/** Why the worker fell back to full visible text. Null outside fallback. */
export type FallbackReason =
  | 'no_article_main_container'
  | 'too_few_qualifying_paragraphs';

/**
 * Provenance of website content, transparently relayed from the worker.
 * Optional so older worker responses and text-mode results stay valid.
 */
export interface ContentSourceInfo {
  contentSource: ContentSource;
  fallbackReason: FallbackReason | null;
}

export interface ContentStatistics {
  words: number;
  characters: number;
  sentences: number;
  paragraphs: number;
  avgSentenceLength: number;
  minSentenceLength: number;
  maxSentenceLength: number;
  vocabularyDiversity: number;
}

export interface Signal {
  id: string;
  name: string;
  level: SignalLevel;
  score: number;
  description: string;
  suggestion?: string;
}

export interface SentenceContribution {
  /** Short label for what triggered this */
  reason: string;
  /** Which signal id it maps to */
  signalId: string;
}

export interface SentenceAnalysis {
  /** The original sentence text */
  text: string;
  /** 0–1 score of how much this sentence contributes to AI-likelihood */
  score: number;
  /** low / medium / high signal level for this sentence */
  level: SignalLevel;
  /** Zero-based index in the original sentence list */
  index: number;
  /** Specific reasons this sentence contributed to the result */
  contributions: SentenceContribution[];
}

export interface AnalysisResult {
  aiLikelihood: number;
  confidence: ConfidenceLevel;
  statistics: ContentStatistics;
  signals: Signal[];
  explanation: string;
  limitations: string;
  /** Per-sentence breakdown (Phase 7) */
  sentenceAnalyses: SentenceAnalysis[];
  /** Source mode — text or website */
  sourceMode?: 'text' | 'website';
  /** Website metadata when sourceMode === 'website' (Phase 9) */
  websiteData?: {
    url: string;
    title: string;
    description: string;
    headings: string[];
    paragraphCount: number;
    wordCount: number;
    /** How the worker produced `content`; absent on legacy worker responses */
    source?: ContentSourceInfo;
  };
}

export interface WebsiteContent {
  url: string;
  title: string;
  description: string;
  content: string;
  wordCount: number;
}

export interface VisibilityDimension {
  id: string
  name: string
  score: number        // 0–100
  weight: number       // dimension weight
  level: 'low' | 'medium' | 'high'
  findings: string[]   // 1–3 evidence sentences
  recommendation?: string
}

export interface VisibilityResult {
  overallScore: number          // 0–100 weighted composite
  confidence: ConfidenceLevel   // reuse existing type
  dimensions: VisibilityDimension[]
  targetTopic?: string
  explanation: string
  limitations: string
}

export interface CombinedResult {
  writing: AnalysisResult
  visibility: VisibilityResult
  sourceMode: 'text' | 'website'
  websiteData?: {
    url: string
    title: string
    description: string
    headings: string[]
    paragraphCount: number
    wordCount: number
    /** How the worker produced `content`; absent on legacy worker responses */
    source?: ContentSourceInfo
  }
  analyzedAt: string
}
