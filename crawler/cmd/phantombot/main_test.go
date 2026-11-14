package main

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/surya-koritala/phantombot/crawler/internal/config"
	"github.com/surya-koritala/phantombot/crawler/internal/fetcher"
	"github.com/surya-koritala/phantombot/crawler/internal/publisher"
	"github.com/surya-koritala/phantombot/crawler/internal/renderer"
	"github.com/surya-koritala/phantombot/crawler/pkg/models"
)

// fakeRenderer is the injectable render client used to drive each render
// outcome in these worker-loop tests.
type fakeRenderer struct {
	resp  *renderer.RenderResponse
	err   error
	calls int
}

func (f *fakeRenderer) Render(ctx context.Context, url string) (*renderer.RenderResponse, error) {
	f.calls++
	return f.resp, f.err
}

func (f *fakeRenderer) IsAvailable(ctx context.Context) bool { return true }

func TestFetchAndRender_Outcomes(t *testing.T) {
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/sparse":
			w.Header().Set("Content-Type", "text/html; charset=utf-8")
			_, _ = w.Write([]byte(`<html><body><div id="root"></div></body></html>`))
		case "/blocked":
			w.WriteHeader(http.StatusForbidden)
			_, _ = w.Write([]byte("<html><body>Forbidden</body></html>"))
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer origin.Close()

	tests := []struct {
		name       string
		path       string
		resp       *renderer.RenderResponse
		err        error
		wantStatus string
		wantRender bool
		wantCode   int
	}{
		{
			name: "success",
			path: "/sparse",
			resp: &renderer.RenderResponse{
				HTML:        `<html><head><title>Full</title></head><body><h1>Loaded</h1></body></html>`,
				StatusCode:  200,
				Title:       "Full",
				ContentType: "text/html; charset=utf-8",
				Headers:     map[string]string{"Content-Type": "text/html; charset=utf-8", "Content-Encoding": "gzip"},
			},
			wantStatus: models.RenderStatusSuccess,
			wantRender: true,
			wantCode:   200,
		},
		{
			name:       "timeout",
			path:       "/blocked",
			err:        &renderer.Error{Kind: renderer.FailureTimeout, Reason: "render request timed out", Err: context.DeadlineExceeded},
			wantStatus: models.RenderStatusTimeout,
			wantCode:   403,
		},
		{
			name:       "error",
			path:       "/sparse",
			err:        &renderer.Error{Kind: renderer.FailureError, Reason: "renderer returned HTTP 500", Err: errors.New("boom")},
			wantStatus: models.RenderStatusError,
			wantCode:   200,
		},
		{
			name:       "unavailable",
			path:       "/blocked",
			err:        &renderer.Error{Kind: renderer.FailureUnavailable, Reason: "renderer unreachable"},
			wantStatus: models.RenderStatusUnavailable,
			wantCode:   403,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			f := fetcher.New("Phantombot/test", 5*time.Second)
			initial, err := f.Fetch(origin.URL+tt.path, 1, "job-1")
			if err != nil {
				t.Fatalf("initial fetch: %v", err)
			}
			originalHTML := initial.HTML
			originalStatus := initial.StatusCode

			injected := &fakeRenderer{resp: tt.resp, err: tt.err}
			fb := renderer.NewFallback(injected, time.Minute)

			// Re-fetch through the helper so the whole path is exercised.
			result, err := fetchAndRender(context.Background(), f, fb.Apply, origin.URL+tt.path, 1)
			if err != nil {
				t.Fatalf("fetchAndRender: %v", err)
			}

			values, err := publisher.MessageValues(result)
			if err != nil {
				t.Fatalf("message values: %v", err)
			}

			if values["render_status"] != tt.wantStatus {
				t.Errorf("render_status = %v, want %q (reason=%v)",
					values["render_status"], tt.wantStatus, values["render_reason"])
			}
			if values["rendered"] != boolString(tt.wantRender) {
				t.Errorf("rendered = %v, want %v", values["rendered"], tt.wantRender)
			}
			if values["render_reason"] == "" {
				t.Error("expected render_reason to be published")
			}
			if values["status_code"] != itoa(tt.wantCode) {
				t.Errorf("status_code = %v, want %d", values["status_code"], tt.wantCode)
			}

			if tt.wantRender {
				if !strings.Contains(values["html"].(string), "Loaded") {
					t.Errorf("expected rendered HTML to be published, got %q", values["html"])
				}
				if values["title"] != "Full" {
					t.Errorf("title = %v, want rendered title", values["title"])
				}
				if _, ok := result.Headers["Content-Encoding"]; ok {
					t.Error("transfer-level response headers must not leak into the rendered result")
				}
			} else {
				// Failure must publish the first fetch unchanged.
				if result.HTML != originalHTML {
					t.Error("expected the first-fetch HTML to be published unchanged")
				}
				if result.StatusCode != originalStatus {
					t.Errorf("expected the first-fetch status %d, got %d", originalStatus, result.StatusCode)
				}
				if result.Title != "" {
					t.Errorf("expected no title on a failed render, got %q", result.Title)
				}
			}
		})
	}
}

func TestFetchAndRender_DoesNotRenderHealthyPages(t *testing.T) {
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html")
		_, _ = w.Write([]byte("<html><body>" + strings.Repeat("<p>Body text</p>", 200) + "</body></html>"))
	}))
	defer origin.Close()

	f := fetcher.New("Phantombot/test", 5*time.Second)
	injected := &fakeRenderer{resp: &renderer.RenderResponse{HTML: "<html>render</html>", StatusCode: 200}}
	fb := renderer.NewFallback(injected, time.Minute)

	result, err := fetchAndRender(context.Background(), f, fb.Apply, origin.URL+"/rich", 0)
	if err != nil {
		t.Fatalf("fetchAndRender: %v", err)
	}
	if injected.calls != 0 {
		t.Errorf("renderer should not be called for a rich page, got %d calls", injected.calls)
	}
	if result.RenderStatus != "" || result.RenderReason != "" {
		t.Errorf("expected no render markers, got status=%q reason=%q", result.RenderStatus, result.RenderReason)
	}
	if result.Rendered {
		t.Error("expected rendered=false")
	}
}

func TestFetchAndRender_NilFallbackKeepsFetcherBehaviour(t *testing.T) {
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusForbidden)
		_, _ = w.Write([]byte("blocked"))
	}))
	defer origin.Close()

	f := fetcher.New("Phantombot/test", 5*time.Second)
	result, err := fetchAndRender(context.Background(), f, nil, origin.URL, 0)
	if err != nil {
		t.Fatalf("fetchAndRender: %v", err)
	}
	if result.StatusCode != 403 || result.HTML != "blocked" {
		t.Errorf("expected plain fetch result, got status=%d html=%q", result.StatusCode, result.HTML)
	}
	if result.RenderStatus != "" || result.RenderReason != "" {
		t.Errorf("expected no render markers, got status=%q reason=%q", result.RenderStatus, result.RenderReason)
	}
}

func boolString(b bool) string {
	if b {
		return "true"
	}
	return "false"
}

func itoa(i int) string {
	return strconv.Itoa(i)
}

func TestNewRenderFallback_NoRendererConfigured(t *testing.T) {
	fb := newRenderFallback(context.Background(), config.Config{RendererURL: ""})
	if fb != nil {
		t.Error("expected no fallback when no renderer URL is configured")
	}
}

func TestNewRenderFallback_ProbesOnceAndRenders(t *testing.T) {
	var healthCalls, renderCalls int
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/health":
			healthCalls++
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write([]byte(`{"status":"ok"}`))
		case "/render":
			renderCalls++
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"url":"http://example.test/sparse","html":"<html><head><title>R</title></head><body>rendered</body></html>","status_code":200,"title":"R","content_type":"text/html; charset=utf-8","headers":{"Content-Type":"text/html; charset=utf-8"}}`))
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer srv.Close()

	fb := newRenderFallback(context.Background(), config.Config{
		RendererURL:      srv.URL,
		RendererTimeout:  2 * time.Second,
		RendererCooldown: time.Minute,
	})
	if fb == nil {
		t.Fatal("expected a fallback")
	}
	if healthCalls != 1 {
		t.Errorf("expected exactly one start-up health probe, got %d", healthCalls)
	}

	for i := 0; i < 3; i++ {
		result := &models.CrawlResult{
			URL:        srv.URL + "/page",
			StatusCode: http.StatusForbidden,
			HTML:       "<html><body>blocked</body></html>",
			Headers:    map[string]string{},
			FetchedAt:  time.Now().UTC(),
		}
		fb(context.Background(), result, result.URL)
		if result.RenderStatus != models.RenderStatusSuccess || !result.Rendered {
			t.Errorf("page %d: expected a successful render, got rendered=%v status=%q",
				i, result.Rendered, result.RenderStatus)
		}
	}
	if renderCalls != 3 {
		t.Errorf("expected 3 render calls, got %d", renderCalls)
	}
	if healthCalls != 1 {
		t.Errorf("availability must be probed once, not per page; got %d health calls", healthCalls)
	}
}

func TestNewRenderFallback_DownRendererDoesNotProbePerPage(t *testing.T) {
	var healthCalls int
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		healthCalls++
		w.WriteHeader(http.StatusServiceUnavailable)
	}))
	defer srv.Close()

	fb := newRenderFallback(context.Background(), config.Config{
		RendererURL:      srv.URL,
		RendererTimeout:  2 * time.Second,
		RendererCooldown: time.Minute,
	})
	if fb == nil {
		t.Fatal("expected a fallback")
	}
	if healthCalls != 1 {
		t.Errorf("expected one start-up probe, got %d", healthCalls)
	}

	for i := 0; i < 3; i++ {
		result := &models.CrawlResult{
			URL:        srv.URL + "/page",
			StatusCode: http.StatusForbidden,
			HTML:       "<html><body>blocked</body></html>",
			Headers:    map[string]string{},
			FetchedAt:  time.Now().UTC(),
		}
		fb(context.Background(), result, result.URL)
		if result.RenderStatus != models.RenderStatusUnavailable {
			t.Errorf("page %d: expected unavailable, got %q", i, result.RenderStatus)
		}
		if result.StatusCode != http.StatusForbidden || result.HTML != "<html><body>blocked</body></html>" {
			t.Errorf("page %d: expected first-fetch result preserved", i)
		}
	}
	if healthCalls != 1 {
		t.Errorf("a down renderer must not be probed per page; got %d health calls", healthCalls)
	}
}
