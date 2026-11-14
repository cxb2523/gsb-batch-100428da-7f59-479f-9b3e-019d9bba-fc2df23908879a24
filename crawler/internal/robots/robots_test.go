package robots

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestChecker_Allowed(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/robots.txt" {
			w.Write([]byte("User-agent: *\nDisallow: /private/\nAllow: /public/\n"))
			return
		}
		w.WriteHeader(200)
	}))
	defer srv.Close()

	c := NewChecker("Phantombot/1.0")

	allowed, err := c.IsAllowed(srv.URL + "/public/page")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !allowed {
		t.Error("expected /public/page to be allowed")
	}

	allowed, err = c.IsAllowed(srv.URL + "/private/secret")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if allowed {
		t.Error("expected /private/secret to be disallowed")
	}
}

func TestChecker_NoRobotsTxt(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/robots.txt" {
			w.WriteHeader(404)
			return
		}
		w.WriteHeader(200)
	}))
	defer srv.Close()

	c := NewChecker("Phantombot/1.0")

	allowed, err := c.IsAllowed(srv.URL + "/anything")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !allowed {
		t.Error("expected all URLs to be allowed when no robots.txt exists")
	}
}

func TestChecker_CachesPerHost(t *testing.T) {
	fetchCount := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/robots.txt" {
			fetchCount++
			w.Write([]byte("User-agent: *\nDisallow:\n"))
			return
		}
	}))
	defer srv.Close()

	c := NewChecker("Phantombot/1.0")

	c.IsAllowed(srv.URL + "/page1")
	c.IsAllowed(srv.URL + "/page2")

	if fetchCount != 1 {
		t.Errorf("expected robots.txt to be fetched once, got %d", fetchCount)
	}
}
