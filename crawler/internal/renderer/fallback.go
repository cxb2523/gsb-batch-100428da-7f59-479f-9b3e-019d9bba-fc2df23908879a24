package renderer

import (
	"context"
	"fmt"
	"strings"
	"sync"
	"time"

	"github.com/surya-koritala/phantombot/crawler/pkg/models"
)

// Renderer is the subset of Client used by the fallback path. It is an
// interface so tests — and callers with their own render backend — can inject
// a deterministic implementation.
type Renderer interface {
	Render(ctx context.Context, url string) (*RenderResponse, error)
	IsAvailable(ctx context.Context) bool
}

// Fallback applies the render-retry policy to results from the plain HTTP
// fetcher. It is safe for concurrent use by multiple workers.
//
// When rendering is triggered, the published result always records the
// outcome in RenderStatus/RenderReason. A successful render replaces the
// page fields with what the browser actually saw; a failed render leaves the
// first fetch's HTML and status code untouched, so pages are never lost and a
// failure is never presented as a success.
//
// After the renderer is found to be unavailable, the Fallback stops calling
// it for Cooldown, so an outage does not add a network timeout to every page.
type Fallback struct {
	renderer Renderer
	cooldown time.Duration
	now      func() time.Time

	mu               sync.Mutex
	unavailableUntil time.Time
	lastReason       string
}

// NewFallback wraps a renderer with the fallback policy. A non-positive
// cooldown disables the circuit breaker.
func NewFallback(r Renderer, cooldown time.Duration) *Fallback {
	return &Fallback{
		renderer: r,
		cooldown: cooldown,
		now:      time.Now,
	}
}

// ProbeAvailability checks the renderer once and records the outcome. Call it
// at start-up instead of probing per page. It returns whether the renderer is
// currently usable.
func (f *Fallback) ProbeAvailability(ctx context.Context) bool {
	if f.renderer == nil {
		f.markUnavailable("no renderer configured")
		return false
	}
	if !f.renderer.IsAvailable(ctx) {
		f.markUnavailable("renderer health check failed")
		return false
	}
	f.clearUnavailable()
	return true
}

// ShouldRender reports whether a fetch result looks like it was blocked or
// served a JS shell, in which case a render retry is worthwhile.
func ShouldRender(result *models.CrawlResult, rawURL string) bool {
	if result == nil {
		return false
	}
	// Blocked by anti-bot.
	if result.StatusCode == 403 || result.StatusCode == 401 {
		return true
	}
	// Very little content (likely a JS-rendered page).
	if result.StatusCode == 200 && len(result.HTML) < 1000 {
		return true
	}
	// URL heuristics suggest an SPA.
	return NeedsRendering(rawURL)
}

// Apply runs the fallback policy for one result, mutating it in place. It is a
// no-op when the page does not need rendering, and it never discards the first
// fetch's HTML or status on failure.
func (f *Fallback) Apply(ctx context.Context, result *models.CrawlResult, rawURL string) {
	if !ShouldRender(result, rawURL) {
		return
	}

	if f.renderer == nil {
		result.Rendered = false
		result.RenderStatus = models.RenderStatusUnavailable
		result.RenderReason = "no renderer configured"
		return
	}

	if until, reason, cooling := f.cooldownActive(); cooling {
		result.Rendered = false
		result.RenderStatus = models.RenderStatusUnavailable
		result.RenderReason = fmt.Sprintf("renderer unavailable, skipping until %s (%s)",
			until.UTC().Format(time.RFC3339), reason)
		return
	}

	rendered, err := f.renderer.Render(ctx, rawURL)
	if err != nil {
		kind, reason := Classify(err)
		status := models.RenderStatusError
		switch kind {
		case FailureUnavailable:
			status = models.RenderStatusUnavailable
			f.markUnavailable(reason)
		case FailureTimeout:
			status = models.RenderStatusTimeout
		}
		result.Rendered = false
		result.RenderStatus = status
		result.RenderReason = reason
		return
	}

	if rendered == nil || rendered.HTML == "" {
		result.Rendered = false
		result.RenderStatus = models.RenderStatusError
		result.RenderReason = "renderer returned an empty document"
		return
	}
	if rendered.StatusCode == 0 {
		result.Rendered = false
		result.RenderStatus = models.RenderStatusError
		result.RenderReason = "renderer returned no status code"
		return
	}

	f.clearUnavailable()
	applyRendered(result, rendered)
}

// applyRendered makes the result describe the rendered page rather than the
// original sparse/blocked HTTP response.
func applyRendered(result *models.CrawlResult, rendered *RenderResponse) {
	result.HTML = rendered.HTML
	result.StatusCode = rendered.StatusCode
	result.Title = rendered.Title

	contentType := rendered.ContentType
	if contentType == "" {
		// Playwright serialises the DOM, so the rendered document is HTML even
		// when the sidecar does not report a content type.
		contentType = "text/html; charset=utf-8"
	}
	result.ContentType = contentType

	// Do not keep headers from the first request: they describe a different
	// response than the HTML being published.
	result.Headers = renderedHeaders(rendered.Headers, contentType)

	result.Rendered = true
	result.RenderStatus = models.RenderStatusSuccess
	result.RenderReason = "rendered by playwright sidecar"
}

// renderedHeaders copies the headers of the rendered response, dropping
// transfer-level headers that describe the encoded network response rather
// than the HTML being published, and guaranteeing a Content-Type.
func renderedHeaders(in map[string]string, contentType string) map[string]string {
	headers := make(map[string]string, len(in)+1)
	hasContentType := false
	for k, v := range in {
		switch strings.ToLower(k) {
		case "content-encoding", "content-length", "transfer-encoding":
			continue
		case "content-type":
			hasContentType = true
		}
		headers[k] = v
	}
	if !hasContentType {
		headers["Content-Type"] = contentType
	}
	return headers
}

func (f *Fallback) cooldownActive() (until time.Time, reason string, active bool) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.unavailableUntil.IsZero() {
		return time.Time{}, "", false
	}
	if f.now().Before(f.unavailableUntil) {
		return f.unavailableUntil, f.lastReason, true
	}
	// Cooldown expired: allow the next page to probe the renderer again.
	f.unavailableUntil = time.Time{}
	return time.Time{}, "", false
}

func (f *Fallback) markUnavailable(reason string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.lastReason = reason
	if f.cooldown > 0 {
		f.unavailableUntil = f.now().Add(f.cooldown)
	}
}

func (f *Fallback) clearUnavailable() {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.unavailableUntil = time.Time{}
	f.lastReason = ""
}
