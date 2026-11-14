package models

import "time"

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
	Rendered    bool              `json:"rendered"`
	Depth       int               `json:"depth"`
	JobID       string            `json:"job_id,omitempty"`
}
