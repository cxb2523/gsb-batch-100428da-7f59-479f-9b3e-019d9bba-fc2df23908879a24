package renderer

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"strings"
	"time"
)

type RenderRequest struct {
	URL       string `json:"url"`
	TimeoutMS int    `json:"timeout_ms,omitempty"`
	WaitUntil string `json:"wait_until,omitempty"`
}

type RenderResponse struct {
	URL        string `json:"url"`
	HTML       string `json:"html"`
	StatusCode int    `json:"status_code"`
	Title      string `json:"title"`
	// ContentType and Headers describe the response the browser actually
	// received. They are optional because older sidecars do not report them.
	ContentType string            `json:"content_type,omitempty"`
	Headers     map[string]string `json:"headers,omitempty"`
}

// FailureKind classifies why a render attempt did not produce a page, so
// callers can report a precise outcome instead of a generic error.
type FailureKind string

const (
	// FailureUnavailable means the renderer service could not be reached.
	// Callers should back off instead of hammering it for every page.
	FailureUnavailable FailureKind = "unavailable"
	// FailureTimeout means the renderer was reached but the render did not
	// finish in time.
	FailureTimeout FailureKind = "timeout"
	// FailureError means the renderer was reached but the render failed.
	FailureError FailureKind = "error"
)

// Error is a classified render failure returned by Client.Render.
type Error struct {
	Kind   FailureKind
	Reason string
	Err    error
}

func (e *Error) Error() string { return e.explain() }

func (e *Error) Unwrap() error { return e.Err }

func (e *Error) explain() string {
	switch {
	case e.Reason == "" && e.Err == nil:
		return string(e.Kind)
	case e.Err == nil:
		return e.Reason
	case e.Reason == "":
		return e.Err.Error()
	default:
		return e.Reason + ": " + e.Err.Error()
	}
}

// Classify maps any render error to a failure kind and a human-readable
// reason. Errors that are not in the *Error form (for example a context
// deadline surfaced by a caller) are still classified sensibly.
func Classify(err error) (FailureKind, string) {
	if err == nil {
		return "", ""
	}
	var re *Error
	if errors.As(err, &re) {
		return re.Kind, re.explain()
	}
	if isTimeout(err) {
		return FailureTimeout, err.Error()
	}
	return FailureError, err.Error()
}

// isTimeout reports whether err is a timeout, including http.Client timeouts.
func isTimeout(err error) bool {
	if errors.Is(err, context.DeadlineExceeded) {
		return true
	}
	var ne net.Error
	return errors.As(err, &ne) && ne.Timeout()
}

// Client calls the Playwright render sidecar.
type Client struct {
	baseURL    string
	httpClient *http.Client
}

func NewClient(baseURL string, timeout time.Duration) *Client {
	return &Client{
		baseURL: baseURL,
		httpClient: &http.Client{
			Timeout: timeout,
		},
	}
}

// IsAvailable checks if the renderer service is running.
func (c *Client) IsAvailable(ctx context.Context) bool {
	req, err := http.NewRequestWithContext(ctx, "GET", c.baseURL+"/health", nil)
	if err != nil {
		return false
	}
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return false
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, resp.Body)
	return resp.StatusCode == 200
}

// Render sends a URL to the Playwright sidecar for JS rendering.
// All failures are returned as *Error so callers can classify them.
func (c *Client) Render(ctx context.Context, url string) (*RenderResponse, error) {
	reqBody, err := json.Marshal(RenderRequest{
		URL:       url,
		WaitUntil: "networkidle",
	})
	if err != nil {
		return nil, &Error{Kind: FailureError, Reason: "marshaling render request", Err: err}
	}

	req, err := http.NewRequestWithContext(ctx, "POST", c.baseURL+"/render", bytes.NewReader(reqBody))
	if err != nil {
		return nil, &Error{Kind: FailureError, Reason: "creating render request", Err: err}
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		switch {
		case errors.Is(err, context.Canceled):
			return nil, &Error{Kind: FailureError, Reason: "render request cancelled", Err: err}
		case isTimeout(err):
			return nil, &Error{Kind: FailureTimeout, Reason: "render request timed out", Err: err}
		default:
			return nil, &Error{Kind: FailureUnavailable, Reason: "renderer unreachable at " + c.baseURL, Err: err}
		}
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		kind := FailureError
		if isTimeout(err) {
			kind = FailureTimeout
		}
		return nil, &Error{Kind: kind, Reason: "reading renderer response", Err: err}
	}

	if resp.StatusCode != http.StatusOK {
		kind := FailureError
		switch {
		case resp.StatusCode == http.StatusRequestTimeout || resp.StatusCode == http.StatusGatewayTimeout:
			kind = FailureTimeout
		case resp.StatusCode == http.StatusServiceUnavailable:
			kind = FailureUnavailable
		case resp.StatusCode == http.StatusInternalServerError && containsTimeout(string(body)):
			// The sidecar reports page-level Playwright timeouts as 500s.
			kind = FailureTimeout
		}
		detail := truncate(strings.TrimSpace(string(body)), 300)
		return nil, &Error{
			Kind:   kind,
			Reason: fmt.Sprintf("renderer returned HTTP %d", resp.StatusCode),
			Err:    errors.New(detail),
		}
	}

	var result RenderResponse
	if err := json.Unmarshal(body, &result); err != nil {
		return nil, &Error{Kind: FailureError, Reason: "parsing renderer response", Err: err}
	}
	if result.HTML == "" {
		return nil, &Error{Kind: FailureError, Reason: "renderer returned an empty document"}
	}

	return &result, nil
}

func containsTimeout(s string) bool {
	return strings.Contains(strings.ToLower(s), "timeout") ||
		strings.Contains(strings.ToLower(s), "timed out")
}

func truncate(s string, max int) string {
	if len(s) <= max {
		return s
	}
	return s[:max] + "..."
}

// NeedsRendering heuristically checks if a URL likely needs JS rendering.
func NeedsRendering(url string) bool {
	// Common SPA frameworks and patterns
	patterns := []string{
		"angular", "react", "vue",
		"app.", "#/",
		"dashboard", "console",
	}
	for _, p := range patterns {
		if contains(url, p) {
			return true
		}
	}
	return false
}

func contains(s, substr string) bool {
	return len(s) >= len(substr) && (s == substr || len(s) > 0 && containsLower(s, substr))
}

func containsLower(s, substr string) bool {
	for i := 0; i <= len(s)-len(substr); i++ {
		if s[i:i+len(substr)] == substr {
			return true
		}
	}
	return false
}
