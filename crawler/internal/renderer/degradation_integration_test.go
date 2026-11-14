package renderer_test

import (
	"context"
	"errors"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/surya-koritala/phantombot/crawler/internal/fetcher"
	"github.com/surya-koritala/phantombot/crawler/internal/publisher"
	"github.com/surya-koritala/phantombot/crawler/internal/renderer"
	"github.com/surya-koritala/phantombot/crawler/pkg/models"
)

// These tests exercise the full degradation path: a plain HTTP fetch against a
// local origin server (sparse or 403), the render retry with an injectable
// renderer client, and the fields that end up in the published stream message.

func newOriginServer(t *testing.T) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/sparse":
			w.Header().Set("Content-Type", "text/html; charset=utf-8")
			_, _ = w.Write([]byte(`<!doctype html><html><head><title>Shell</title></head><body><div id="root"></div><script src="/bundle.js"></script></body></html>`))
		case "/blocked":
			w.Header().Set("Content-Type", "text/html")
			w.WriteHeader(http.StatusForbidden)
			_, _ = w.Write([]byte("<html><body>Forbidden by edge</body></html>"))
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	t.Cleanup(srv.Close)
	return srv
}

type renderOutcome struct {
	resp *renderer.RenderResponse
	err  error
}

type injectedRenderer struct {
	outcome renderOutcome
	calls   int
}

func (r *injectedRenderer) Render(ctx context.Context, url string) (*renderer.RenderResponse, error) {
	r.calls++
	return r.outcome.resp, r.outcome.err
}

func (r *injectedRenderer) IsAvailable(ctx context.Context) bool { return true }

func fetchFirst(t *testing.T, origin, path string) *models.CrawlResult {
	t.Helper()
	f := fetcher.New("Phantombot/test", 5*time.Second)
	result, err := f.Fetch(origin+path, 1, "job-1")
	if err != nil {
		t.Fatalf("fetch %s: %v", path, err)
	}
	return result
}

func publishValues(t *testing.T, result *models.CrawlResult) map[string]any {
	t.Helper()
	values, err := publisher.MessageValues(result)
	if err != nil {
		t.Fatalf("building message: %v", err)
	}
	return values
}

func TestDegradationPath_SparsePage_RenderSucceeds(t *testing.T) {
	origin := newOriginServer(t)
	first := fetchFirst(t, origin.URL, "/sparse")

	injected := &injectedRenderer{outcome: renderOutcome{resp: &renderer.RenderResponse{
		URL:         origin.URL + "/sparse",
		HTML:        `<html><head><title>Full Page</title></head><body><h1>Hydrated content</h1></body></html>`,
		StatusCode:  200,
		Title:       "Full Page",
		ContentType: "text/html; charset=utf-8",
		Headers:     map[string]string{"Content-Type": "text/html; charset=utf-8"},
	}}}

	fb := renderer.NewFallback(injected, time.Minute)
	fb.Apply(context.Background(), first, first.URL)

	values := publishValues(t, first)
	if values["rendered"] != "true" {
		t.Errorf("rendered = %v, want true", values["rendered"])
	}
	if values["render_status"] != models.RenderStatusSuccess {
		t.Errorf("render_status = %v, want %q", values["render_status"], models.RenderStatusSuccess)
	}
	if values["render_reason"] == "" {
		t.Error("expected render_reason to be published")
	}
	if values["title"] != "Full Page" {
		t.Errorf("title = %v, want the rendered title", values["title"])
	}
	if !strings.Contains(values["html"].(string), "Hydrated content") {
		t.Errorf("expected rendered HTML to be published, got %q", values["html"])
	}
	if values["status_code"] != "200" {
		t.Errorf("status_code = %v, want 200", values["status_code"])
	}
	if injected.calls != 1 {
		t.Errorf("expected 1 render attempt, got %d", injected.calls)
	}
}

func TestDegradationPath_FailureKindsKeepFirstFetch(t *testing.T) {
	origin := newOriginServer(t)

	tests := []struct {
		name       string
		path       string
		err        error
		wantStatus string
		wantCode   string
		wantHTML   string
	}{
		{
			name:       "403 page, render times out",
			path:       "/blocked",
			err:        &renderer.Error{Kind: renderer.FailureTimeout, Reason: "render request timed out", Err: context.DeadlineExceeded},
			wantStatus: models.RenderStatusTimeout,
			wantCode:   "403",
			wantHTML:   "Forbidden by edge",
		},
		{
			name:       "sparse page, render errors",
			path:       "/sparse",
			err:        &renderer.Error{Kind: renderer.FailureError, Reason: "renderer returned HTTP 500", Err: errors.New("boom")},
			wantStatus: models.RenderStatusError,
			wantCode:   "200",
			wantHTML:   `id="root"`,
		},
		{
			name:       "403 page, renderer unavailable",
			path:       "/blocked",
			err:        &renderer.Error{Kind: renderer.FailureUnavailable, Reason: "renderer unreachable"},
			wantStatus: models.RenderStatusUnavailable,
			wantCode:   "403",
			wantHTML:   "Forbidden by edge",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			first := fetchFirst(t, origin.URL, tt.path)
			originalHTML := first.HTML
			originalStatus := first.StatusCode

			injected := &injectedRenderer{outcome: renderOutcome{err: tt.err}}
			fb := renderer.NewFallback(injected, time.Minute)
			fb.Apply(context.Background(), first, first.URL)

			values := publishValues(t, first)
			if values["rendered"] != "false" {
				t.Errorf("rendered = %v, want false", values["rendered"])
			}
			if values["render_status"] != tt.wantStatus {
				t.Errorf("render_status = %v, want %q", values["render_status"], tt.wantStatus)
			}
			if values["render_reason"] == "" {
				t.Error("expected render_reason to be published")
			}
			if values["status_code"] != tt.wantCode {
				t.Errorf("status_code = %v, want %q (first fetch)", values["status_code"], tt.wantCode)
			}
			if values["html"] != originalHTML || first.HTML != originalHTML {
				t.Errorf("expected first-fetch HTML to be published unchanged")
			}
			if first.StatusCode != originalStatus {
				t.Errorf("expected first-fetch status %d, got %d", originalStatus, first.StatusCode)
			}
			if !strings.Contains(values["html"].(string), tt.wantHTML) {
				t.Errorf("published HTML = %q, want it to contain %q", values["html"], tt.wantHTML)
			}
		})
	}
}

func TestDegradationPath_NonRenderPathUnchanged(t *testing.T) {
	// A rich page that does not need rendering must be published exactly as
	// fetched, with no render markers and no renderer calls.
	rich := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		_, _ = w.Write([]byte("<html><body>" + strings.Repeat("<p>plenty of content</p>", 100) + "</body></html>"))
	}))
	defer rich.Close()

	f := fetcher.New("Phantombot/test", 5*time.Second)
	first, err := f.Fetch(rich.URL+"/article", 2, "")
	if err != nil {
		t.Fatalf("fetch: %v", err)
	}
	originalHTML := first.HTML

	injected := &injectedRenderer{outcome: renderOutcome{resp: &renderer.RenderResponse{
		HTML: "<html><body>rendered</body></html>", StatusCode: 200,
	}}}
	fb := renderer.NewFallback(injected, time.Minute)
	fb.Apply(context.Background(), first, first.URL)

	values := publishValues(t, first)
	if injected.calls != 0 {
		t.Errorf("expected no render attempts for a rich page, got %d", injected.calls)
	}
	if values["rendered"] != "false" {
		t.Errorf("rendered = %v, want false", values["rendered"])
	}
	if values["render_status"] != "" || values["render_reason"] != "" {
		t.Errorf("expected empty render markers, got status=%v reason=%v",
			values["render_status"], values["render_reason"])
	}
	if first.HTML != originalHTML || values["html"] != originalHTML {
		t.Error("expected HTML untouched on the non-render path")
	}
	if first.ContentType != "text/html; charset=utf-8" {
		t.Errorf("content_type = %q, want first fetch value", first.ContentType)
	}
}

func TestDegradationPath_RealClientAgainstDownRenderer(t *testing.T) {
	origin := newOriginServer(t)
	first := fetchFirst(t, origin.URL, "/blocked")

	// Reserve a port, then close it so connections are refused.
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	addr := l.Addr().String()
	_ = l.Close()

	client := renderer.NewClient("http://"+addr, 300*time.Millisecond)
	fb := renderer.NewFallback(client, time.Minute)
	fb.Apply(context.Background(), first, first.URL)

	values := publishValues(t, first)
	if values["render_status"] != models.RenderStatusUnavailable {
		t.Errorf("render_status = %v, want %q", values["render_status"], models.RenderStatusUnavailable)
	}
	if values["rendered"] != "false" {
		t.Errorf("rendered = %v, want false", values["rendered"])
	}
	if values["status_code"] != "403" {
		t.Errorf("status_code = %v, want 403 from the first fetch", values["status_code"])
	}
	if !strings.Contains(values["html"].(string), "Forbidden by edge") {
		t.Errorf("expected first-fetch HTML, got %q", values["html"])
	}
}
