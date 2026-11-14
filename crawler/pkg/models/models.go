package models

import "time"

// Render outcome values recorded on CrawlResult.RenderStatus.
//
// An empty status means rendering was never triggered for the page (the first
// HTTP fetch was good enough). Every non-empty status means the render
// fallback path was triggered, and RenderReason explains what happened.
const (
	// RenderStatusSuccess means the Playwright sidecar rendered the page and
	// its HTML/status/title/content type were used for the published result.
	RenderStatusSuccess = "success"
	// RenderStatusUnavailable means the renderer could not be reached (or was
	// skipped because it was known to be unavailable). The first fetch result
	// is published unchanged.
	RenderStatusUnavailable = "unavailable"
	// RenderStatusTimeout means the renderer was reached but did not finish in
	// time. The first fetch result is published unchanged.
	RenderStatusTimeout = "timeout"
	// RenderStatusError means the renderer was reached but the render failed
	// (bad response, browser error, ...). The first fetch result is published
	// unchanged.
	RenderStatusError = "error"
)

// CrawlRequest represents a URL to be crawled.
type CrawlRequest struct {
	URL      string `json:"url"`
	Depth    int    `json:"depth"`
	MaxDepth int    `json:"max_depth"`
	JobID    string `json:"job_id,omitempty"`
}

// CrawlResult is the output of fetching a single URL.
// This maps directly to the Redis Streams message format.
type CrawlResult struct {
	URL         string            `json:"url"`
	StatusCode  int               `json:"status_code"`
	ContentType string            `json:"content_type"`
	HTML        string            `json:"html"`
	Headers     map[string]string `json:"headers"`
	FetchedAt   time.Time         `json:"fetched_at"`
	// Rendered is true only when the published HTML/status came from a
	// successful Playwright render.
	Rendered bool `json:"rendered"`
	// Title is the page title. It is populated from the renderer when
	// rendering succeeds and is empty for plain HTTP fetches.
	Title string `json:"title,omitempty"`
	// RenderStatus is the outcome of the render fallback attempt. It is empty
	// when rendering was not triggered, otherwise one of the RenderStatus*
	// constants above.
	RenderStatus string `json:"render_status,omitempty"`
	// RenderReason is a human-readable explanation of RenderStatus.
	RenderReason string `json:"render_reason,omitempty"`
	Depth        int    `json:"depth"`
	JobID        string `json:"job_id,omitempty"`
}
