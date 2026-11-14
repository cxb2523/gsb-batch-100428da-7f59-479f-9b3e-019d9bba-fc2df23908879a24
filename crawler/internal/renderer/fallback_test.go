package renderer

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/surya-koritala/phantombot/crawler/pkg/models"
)

// fakeRenderer is an injectable renderer used to drive each fallback outcome
// deterministically, without a real Playwright service.
type fakeRenderer struct {
	mu         sync.Mutex
	response   *RenderResponse
	err        error
	available  bool
	calls      int
	availCalls int
}

func (f *fakeRenderer) Render(ctx context.Context, url string) (*RenderResponse, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.calls++
	if f.err != nil {
		return nil, f.err
	}
	return f.response, nil
}

func (f *fakeRenderer) IsAvailable(ctx context.Context) bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.availCalls++
	return f.available
}

func (f *fakeRenderer) callCount() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.calls
}

func sparseResult() *models.CrawlResult {
	return &models.CrawlResult{
		URL:         "http://example.test/sparse",
		StatusCode:  200,
		ContentType: "text/html; charset=utf-8",
		HTML:        `<html><body><div id="root"></div><script src="/app.js"></script></body></html>`,
		Headers:     map[string]string{"Server": "nginx", "Content-Type": "text/html; charset=utf-8"},
		FetchedAt:   time.Date(2026, 1, 2, 3, 4, 5, 0, time.UTC),
	}
}

func blockedResult() *models.CrawlResult {
	return &models.CrawlResult{
		URL:         "http://example.test/blocked",
		StatusCode:  403,
		ContentType: "text/html",
		HTML:        "<html><body>Access denied</body></html>",
		Headers:     map[string]string{"Server": "cloudflare", "Content-Type": "text/html"},
		FetchedAt:   time.Date(2026, 1, 2, 3, 4, 5, 0, time.UTC),
	}
}

func TestFallback_SuccessReplacesPageFields(t *testing.T) {
	fake := &fakeRenderer{
		available: true,
		response: &RenderResponse{
			URL:         "http://example.test/sparse",
			HTML:        "<html><head><title>Real Title</title></head><body><h1>Rendered Content</h1></body></html>",
			StatusCode:  200,
			Title:       "Real Title",
			ContentType: "text/html; charset=utf-8",
			Headers:     map[string]string{"Content-Type": "text/html; charset=utf-8", "Server": "playwright-host"},
		},
	}
	fb := NewFallback(fake, time.Minute)
	result := sparseResult()

	fb.Apply(context.Background(), result, result.URL)

	if !result.Rendered {
		t.Error("expected Rendered=true after successful render")
	}
	if result.RenderStatus != models.RenderStatusSuccess {
		t.Errorf("expected render_status %q, got %q", models.RenderStatusSuccess, result.RenderStatus)
	}
	if result.RenderReason == "" {
		t.Error("expected a non-empty render_reason on success")
	}
	if !strings.Contains(result.HTML, "Rendered Content") {
		t.Errorf("expected rendered HTML, got %q", result.HTML)
	}
	if result.StatusCode != 200 {
		t.Errorf("expected rendered status 200, got %d", result.StatusCode)
	}
	if result.Title != "Real Title" {
		t.Errorf("expected title from renderer, got %q", result.Title)
	}
	if result.ContentType != "text/html; charset=utf-8" {
		t.Errorf("expected content type from renderer, got %q", result.ContentType)
	}
	if result.Headers["Server"] != "playwright-host" {
		t.Errorf("expected headers to describe the rendered response, got %v", result.Headers)
	}
	// Fetch metadata must be preserved.
	if result.Depth != 0 || !result.FetchedAt.Equal(time.Date(2026, 1, 2, 3, 4, 5, 0, time.UTC)) {
		t.Errorf("expected fetch metadata preserved, got depth=%d fetched_at=%v", result.Depth, result.FetchedAt)
	}
}

func TestFallback_SuccessDefaultsContentTypeWhenSidecarOmitsIt(t *testing.T) {
	fake := &fakeRenderer{
		available: true,
		response: &RenderResponse{
			HTML:       "<html><body>rendered</body></html>",
			StatusCode: 200,
			Title:      "T",
		},
	}
	fb := NewFallback(fake, time.Minute)
	result := blockedResult()
	result.StatusCode = 200 // sparse 200 page

	fb.Apply(context.Background(), result, result.URL)

	if result.ContentType != "text/html; charset=utf-8" {
		t.Errorf("expected HTML content type, got %q", result.ContentType)
	}
	if result.Headers["Content-Type"] != "text/html; charset=utf-8" {
		t.Errorf("expected Content-Type header, got %v", result.Headers)
	}
	if result.Rendered != true || result.RenderStatus != models.RenderStatusSuccess {
		t.Errorf("expected successful render, got rendered=%v status=%q", result.Rendered, result.RenderStatus)
	}
}

func TestFallback_TimeoutKeepsFirstFetch(t *testing.T) {
	fake := &fakeRenderer{
		available: true,
		err:       &Error{Kind: FailureTimeout, Reason: "render request timed out", Err: context.DeadlineExceeded},
	}
	fb := NewFallback(fake, time.Minute)
	result := blockedResult()

	fb.Apply(context.Background(), result, result.URL)

	if result.Rendered {
		t.Error("a timed-out render must not be marked as rendered")
	}
	if result.RenderStatus != models.RenderStatusTimeout {
		t.Errorf("expected render_status %q, got %q", models.RenderStatusTimeout, result.RenderStatus)
	}
	if !strings.Contains(result.RenderReason, "timed out") {
		t.Errorf("expected timeout reason, got %q", result.RenderReason)
	}
	if result.StatusCode != 403 {
		t.Errorf("expected first-fetch status 403 preserved, got %d", result.StatusCode)
	}
	if result.HTML != "<html><body>Access denied</body></html>" {
		t.Errorf("expected first-fetch HTML preserved, got %q", result.HTML)
	}
	if result.ContentType != "text/html" {
		t.Errorf("expected first-fetch content type preserved, got %q", result.ContentType)
	}
	if result.Headers["Server"] != "cloudflare" {
		t.Errorf("expected first-fetch headers preserved, got %v", result.Headers)
	}
}

func TestFallback_RenderErrorKeepsFirstFetch(t *testing.T) {
	fake := &fakeRenderer{
		available: true,
		err:       &Error{Kind: FailureError, Reason: "renderer returned HTTP 500", Err: errors.New("Render failed: boom")},
	}
	fb := NewFallback(fake, time.Minute)
	result := sparseResult()

	fb.Apply(context.Background(), result, result.URL)

	if result.Rendered {
		t.Error("a failed render must not be marked as rendered")
	}
	if result.RenderStatus != models.RenderStatusError {
		t.Errorf("expected render_status %q, got %q", models.RenderStatusError, result.RenderStatus)
	}
	if !strings.Contains(result.RenderReason, "500") {
		t.Errorf("expected error reason mentioning the status, got %q", result.RenderReason)
	}
	if result.StatusCode != 200 || !strings.Contains(result.HTML, `id="root"`) {
		t.Errorf("expected first-fetch HTML/status preserved, got status=%d html=%q", result.StatusCode, result.HTML)
	}
}

func TestFallback_UnavailableKeepsFirstFetch(t *testing.T) {
	fake := &fakeRenderer{
		available: true,
		err:       &Error{Kind: FailureUnavailable, Reason: "renderer unreachable at http://localhost:1"},
	}
	fb := NewFallback(fake, time.Minute)
	result := blockedResult()

	fb.Apply(context.Background(), result, result.URL)

	if result.Rendered {
		t.Error("an unavailable renderer must not be marked as rendered")
	}
	if result.RenderStatus != models.RenderStatusUnavailable {
		t.Errorf("expected render_status %q, got %q", models.RenderStatusUnavailable, result.RenderStatus)
	}
	if result.RenderReason == "" {
		t.Error("expected a non-empty render_reason")
	}
	if result.StatusCode != 403 || !strings.Contains(result.HTML, "Access denied") {
		t.Errorf("expected first-fetch HTML/status preserved, got status=%d html=%q", result.StatusCode, result.HTML)
	}
}

func TestFallback_UnavailableStopsProbingEveryPage(t *testing.T) {
	fake := &fakeRenderer{
		available: true,
		err:       &Error{Kind: FailureUnavailable, Reason: "connection refused"},
	}
	now := time.Date(2026, 1, 2, 3, 4, 5, 0, time.UTC)
	fb := NewFallback(fake, time.Minute)
	fb.now = func() time.Time { return now }

	first := blockedResult()
	fb.Apply(context.Background(), first, first.URL)
	if fake.callCount() != 1 {
		t.Fatalf("expected 1 render attempt, got %d", fake.callCount())
	}

	for i := 0; i < 5; i++ {
		r := sparseResult()
		fb.Apply(context.Background(), r, r.URL)
		if r.RenderStatus != models.RenderStatusUnavailable {
			t.Errorf("page %d: expected render_status unavailable during cooldown, got %q", i, r.RenderStatus)
		}
		if r.Rendered {
			t.Errorf("page %d: must not be rendered during cooldown", i)
		}
		if r.StatusCode != 200 || !strings.Contains(r.HTML, `id="root"`) {
			t.Errorf("page %d: expected first-fetch result preserved", i)
		}
	}
	if fake.callCount() != 1 {
		t.Errorf("renderer should not be probed during cooldown; got %d calls", fake.callCount())
	}

	// After the cooldown expires the renderer is probed again.
	now = now.Add(2 * time.Minute)
	fake.mu.Lock()
	fake.err = nil
	fake.response = &RenderResponse{HTML: "<html><body>ok</body></html>", StatusCode: 200, Title: "ok"}
	fake.mu.Unlock()

	recovered := sparseResult()
	fb.Apply(context.Background(), recovered, recovered.URL)
	if fake.callCount() != 2 {
		t.Errorf("expected a retry after cooldown, got %d calls", fake.callCount())
	}
	if recovered.RenderStatus != models.RenderStatusSuccess || !recovered.Rendered {
		t.Errorf("expected recovery to render successfully, got rendered=%v status=%q",
			recovered.Rendered, recovered.RenderStatus)
	}
}

func TestFallback_SkipsWhenNoRenderNeeded(t *testing.T) {
	fake := &fakeRenderer{
		available: true,
		response:  &RenderResponse{HTML: "<html><body>rendered</body></html>", StatusCode: 200},
	}
	fb := NewFallback(fake, time.Minute)

	result := &models.CrawlResult{
		URL:         "http://example.test/article",
		StatusCode:  200,
		ContentType: "text/html",
		HTML:        strings.Repeat("<p>Long article content.</p>", 100),
		Headers:     map[string]string{"Content-Type": "text/html"},
		FetchedAt:   time.Date(2026, 1, 2, 3, 4, 5, 0, time.UTC),
	}

	fb.Apply(context.Background(), result, result.URL)

	if fake.callCount() != 0 {
		t.Errorf("renderer should not be called for healthy pages, got %d calls", fake.callCount())
	}
	if result.RenderStatus != "" || result.RenderReason != "" {
		t.Errorf("expected no render markers for an untouched result, got status=%q reason=%q",
			result.RenderStatus, result.RenderReason)
	}
	if result.Rendered {
		t.Error("expected rendered=false")
	}
	if result.HTML != strings.Repeat("<p>Long article content.</p>", 100) {
		t.Error("expected HTML untouched")
	}
}

func TestFallback_EmptyRenderedDocumentIsAnError(t *testing.T) {
	fake := &fakeRenderer{
		available: true,
		response:  &RenderResponse{HTML: "", StatusCode: 200},
	}
	fb := NewFallback(fake, time.Minute)
	result := blockedResult()

	fb.Apply(context.Background(), result, result.URL)

	if result.Rendered {
		t.Error("an empty rendered document must not be marked as rendered")
	}
	if result.RenderStatus != models.RenderStatusError {
		t.Errorf("expected render_status %q, got %q", models.RenderStatusError, result.RenderStatus)
	}
	if result.StatusCode != 403 {
		t.Errorf("expected first-fetch status preserved, got %d", result.StatusCode)
	}
}

func TestFallback_ProbeAvailability(t *testing.T) {
	fake := &fakeRenderer{available: false}
	fb := NewFallback(fake, time.Minute)
	if fb.ProbeAvailability(context.Background()) {
		t.Error("expected unavailable renderer")
	}

	r := blockedResult()
	fb.Apply(context.Background(), r, r.URL)
	if r.RenderStatus != models.RenderStatusUnavailable {
		t.Errorf("expected unavailable status after failed probe, got %q", r.RenderStatus)
	}
	if fake.callCount() != 0 {
		t.Errorf("expected no render calls while unavailable, got %d", fake.callCount())
	}
}

func TestClassify(t *testing.T) {
	tests := []struct {
		name string
		err  error
		want FailureKind
	}{
		{"classified timeout", &Error{Kind: FailureTimeout}, FailureTimeout},
		{"classified unavailable", &Error{Kind: FailureUnavailable}, FailureUnavailable},
		{"context deadline", context.DeadlineExceeded, FailureTimeout},
		{"generic error", errors.New("boom"), FailureError},
	}
	for _, tt := range tests {
		got, reason := Classify(tt.err)
		if got != tt.want {
			t.Errorf("%s: Classify() = %q, want %q", tt.name, got, tt.want)
		}
		if reason == "" {
			t.Errorf("%s: expected a reason", tt.name)
		}
	}
}

// End-to-end-ish fallback test against a real local renderer service over
// HTTP, with the renderer client injected into the fallback.
func TestFallback_WithHTTPRendererService(t *testing.T) {
	tests := []struct {
		name       string
		handler    http.HandlerFunc
		clientTime time.Duration
		wantStatus string
		wantRender bool
	}{
		{
			name: "success",
			handler: func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				_, _ = w.Write([]byte(`{"url":"http://example.test/sparse","html":"<html><head><title>Done</title></head><body>Full</body></html>","status_code":200,"title":"Done","content_type":"text/html; charset=utf-8","headers":{"Content-Type":"text/html; charset=utf-8"}}`))
			},
			clientTime: 5 * time.Second,
			wantStatus: models.RenderStatusSuccess,
			wantRender: true,
		},
		{
			name: "timeout",
			handler: func(w http.ResponseWriter, r *http.Request) {
				time.Sleep(300 * time.Millisecond)
				_, _ = w.Write([]byte(`{}`))
			},
			clientTime: 50 * time.Millisecond,
			wantStatus: models.RenderStatusTimeout,
		},
		{
			name: "error",
			handler: func(w http.ResponseWriter, r *http.Request) {
				w.WriteHeader(http.StatusInternalServerError)
				_, _ = w.Write([]byte("Render failed: browser crashed"))
			},
			clientTime: 5 * time.Second,
			wantStatus: models.RenderStatusError,
		},
		{
			name: "unavailable",
			handler: func(w http.ResponseWriter, r *http.Request) {
				w.WriteHeader(http.StatusServiceUnavailable)
				_, _ = w.Write([]byte("Browser not initialized"))
			},
			clientTime: 5 * time.Second,
			wantStatus: models.RenderStatusUnavailable,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			srv := httptest.NewServer(tt.handler)
			defer srv.Close()

			client := NewClient(srv.URL, tt.clientTime)
			fb := NewFallback(client, time.Minute)

			result := sparseResult()
			fb.Apply(context.Background(), result, result.URL)

			if result.RenderStatus != tt.wantStatus {
				t.Errorf("expected render_status %q, got %q (reason=%q)",
					tt.wantStatus, result.RenderStatus, result.RenderReason)
			}
			if result.Rendered != tt.wantRender {
				t.Errorf("expected rendered=%v, got %v", tt.wantRender, result.Rendered)
			}
			if result.RenderReason == "" {
				t.Error("expected a non-empty render_reason")
			}
			if !tt.wantRender {
				// Failures must publish the first fetch unchanged.
				if result.StatusCode != 200 || !strings.Contains(result.HTML, `id="root"`) {
					t.Errorf("expected first-fetch HTML/status preserved, got status=%d html=%q",
						result.StatusCode, result.HTML)
				}
			}
		})
	}
}

func TestFallback_UnreachableRendererService(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	url := srv.URL
	srv.Close() // now nothing is listening

	client := NewClient(url, time.Second)
	fb := NewFallback(client, time.Minute)
	result := blockedResult()

	fb.Apply(context.Background(), result, result.URL)

	if result.RenderStatus != models.RenderStatusUnavailable {
		t.Errorf("expected render_status %q, got %q", models.RenderStatusUnavailable, result.RenderStatus)
	}
	if result.Rendered {
		t.Error("expected rendered=false")
	}
	if result.StatusCode != 403 || !strings.Contains(result.HTML, "Access denied") {
		t.Errorf("expected first-fetch result preserved, got status=%d html=%q", result.StatusCode, result.HTML)
	}
}
