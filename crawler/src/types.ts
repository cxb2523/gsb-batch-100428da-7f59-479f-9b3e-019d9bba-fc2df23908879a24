export const providers = [
  "ashby",
  "bamboohr",
  "greenhouse",
  "icims",
  "lever",
  "smartrecruiters",
  "teamtailor",
  "workable",
  "workday",
] as const;

export type Provider = (typeof providers)[number];

export type IdentifierSource = {
  identifier: string;
};

export type WorkdaySource = {
  tenant: string;
  shard: string;
  site: string;
};

export type SourceEntry = IdentifierSource | WorkdaySource;

export type SourceFile = {
  provider: Provider;
  url_template?: string;
  jobid_template?: string;
  companies: SourceEntry[];
};

export type NormalizedJob = {
  provider: Provider;
  source_key: string;
  job_id: string;
  title: string | null;
  location: string | null;
  employment_type: string | null;
  compensation: string | null;
  department: string | null;
  office: string | null;
  language: string | null;
  updated_at: string | null;
  posted_at: string | null;
  job_url: string | null;
  fetched_at: string;
  skill_tier?: string | null;
};

export type CrawlFailure = {
  provider: Provider;
  source_key: string;
  status?: number;
  message: string;
  // Present (and true) when the source had already emitted one or more jobs
  // before the crawl errored, e.g. a failure on a later pagination page.
  partial?: boolean;
  // Number of jobs from this source that were written to the JSONL output
  // before the error. Only set alongside partial: true.
  jobs_emitted?: number;
};

export type SourceExclusion = {
  provider: Provider;
  source_key: string;
  reason?: string;
  last_http_status?: number;
  last_seen_at?: string;
};

export type ProviderStats = {
  sources: number;
  skipped: number;
  succeeded: number;
  // Sources that emitted at least one job but still raised an error afterwards.
  partial: number;
  // Sources that raised an error without emitting any jobs.
  failed: number;
  jobs: number;
};

export type CrawlReport = {
  started_at: string;
  ended_at: string;
  source_counts: Record<Provider, number>;
  skipped_sources: number;
  skipped_by_provider: Record<Provider, number>;
  providers: Record<Provider, ProviderStats>;
  // Total sources across providers that emitted some jobs but still errored.
  partial_sources: number;
  total_jobs: number;
  failures: CrawlFailure[];
};

export type HttpClient = {
  getJson<T = unknown>(url: string, init?: RequestInit): Promise<T>;
  postJson<T = unknown>(url: string, body: unknown, init?: RequestInit): Promise<T>;
  getText(url: string, init?: RequestInit): Promise<string>;
};

export type CrawlContext = {
  http: HttpClient;
  fetchedAt: () => string;
  maxJobsPerSource?: number;
  urlTemplate?: string;
  jobIdTemplate?: string;
  // Streams already-fetched pages to the runner so jobs are persisted even when
  // a later request (e.g. the next pagination page) fails. Paginating crawlers
  // call this after normalizing each page. May return a promise that resolves
  // once the jobs have been written to the JSONL output.
  onJobs?: (jobs: NormalizedJob[]) => void | Promise<void>;
};

export type ProviderCrawler = {
  provider: Provider;
  crawl(source: SourceEntry, context: CrawlContext): Promise<NormalizedJob[]>;
};
