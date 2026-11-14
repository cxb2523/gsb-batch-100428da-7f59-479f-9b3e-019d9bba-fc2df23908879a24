package fetcher

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestFetcher_FetchSuccess(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html")
		w.Write([]byte("<html><body>Hello</body></html>"))
	}))
	defer srv.Close()

	f := New("Phantombot/1.0", 10*time.Second)
	result, err := f.Fetch(srv.URL+"/page", 0, "")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if result.StatusCode != 200 {
		t.Errorf("expected status 200, got %d", result.StatusCode)
	}
	if result.HTML != "<html><body>Hello</body></html>" {
		t.Errorf("unexpected HTML: %s", result.HTML)
	}
	if result.ContentType != "text/html" {
		t.Errorf("expected content-type text/html, got %s", result.ContentType)
	}
	if result.URL != srv.URL+"/page" {
		t.Errorf("expected URL %s, got %s", srv.URL+"/page", result.URL)
	}
	if result.Rendered {
		t.Error("expected rendered=false")
	}
}

func TestFetcher_FollowsRedirects(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/old" {
			http.Redirect(w, r, "/new", http.StatusMovedPermanently)
			return
		}
		w.Write([]byte("final page"))
	}))
	defer srv.Close()

	f := New("Phantombot/1.0", 10*time.Second)
	result, err := f.Fetch(srv.URL+"/old", 0, "")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if result.StatusCode != 200 {
		t.Errorf("expected status 200 after redirect, got %d", result.StatusCode)
	}
	if result.HTML != "final page" {
		t.Errorf("unexpected HTML: %s", result.HTML)
	}
}

func TestFetcher_Timeout(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(2 * time.Second)
		w.Write([]byte("slow"))
	}))
	defer srv.Close()

	f := New("Phantombot/1.0", 100*time.Millisecond)
	_, err := f.Fetch(srv.URL, 0, "")
	if err == nil {
		t.Fatal("expected timeout error")
	}
}

func TestFetcher_SetsUserAgent(t *testing.T) {
	var gotUA string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotUA = r.Header.Get("User-Agent")
		w.Write([]byte("ok"))
	}))
	defer srv.Close()

	f := New("Phantombot/1.0-test", 10*time.Second)
	f.Fetch(srv.URL, 0, "")

	if gotUA != "Phantombot/1.0-test" {
		t.Errorf("expected User-Agent 'Phantombot/1.0-test', got %q", gotUA)
	}
}

func TestFetcher_ServerError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(500)
		w.Write([]byte("internal error"))
	}))
	defer srv.Close()

	f := New("Phantombot/1.0", 10*time.Second)
	result, err := f.Fetch(srv.URL, 0, "")
	if err != nil {
		t.Fatalf("server errors should not return Go errors: %v", err)
	}
	if result.StatusCode != 500 {
		t.Errorf("expected status 500, got %d", result.StatusCode)
	}
}

func TestFetcher_StealthHeaders(t *testing.T) {
	var gotHeaders map[string]string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotHeaders = make(map[string]string)
		for k := range r.Header {
			gotHeaders[k] = r.Header.Get(k)
		}
		w.Write([]byte("ok"))
	}))
	defer srv.Close()

	f := NewWithOptions("", 10*time.Second, true, "")
	f.Fetch(srv.URL, 0, "")

	// Should have stealth headers
	if _, ok := gotHeaders["Accept"]; !ok {
		t.Error("expected Accept header in stealth mode")
	}
	if _, ok := gotHeaders["Accept-Language"]; !ok {
		t.Error("expected Accept-Language header in stealth mode")
	}
}

func TestFetcher_StealthRotatesUA(t *testing.T) {
	var agents []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		agents = append(agents, r.Header.Get("User-Agent"))
		w.Write([]byte("ok"))
	}))
	defer srv.Close()

	f := NewWithOptions("", 10*time.Second, true, "")
	for i := 0; i < 20; i++ {
		f.Fetch(srv.URL, 0, "")
	}

	// Should see multiple different UAs
	seen := make(map[string]bool)
	for _, ua := range agents {
		seen[ua] = true
	}
	if len(seen) < 2 {
		t.Errorf("expected UA rotation, only saw %d unique agents", len(seen))
	}
}
