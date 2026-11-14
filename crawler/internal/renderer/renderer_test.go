package renderer

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestClient_IsAvailable(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/health" {
			json.NewEncoder(w).Encode(map[string]any{"status": "ok"})
			return
		}
	}))
	defer srv.Close()

	client := NewClient(srv.URL, 5*time.Second)
	if !client.IsAvailable(context.Background()) {
		t.Error("expected available")
	}
}

func TestClient_IsAvailable_Down(t *testing.T) {
	client := NewClient("http://localhost:99999", 1*time.Second)
	if client.IsAvailable(context.Background()) {
		t.Error("expected not available")
	}
}

func TestClient_Render(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/render" {
			json.NewEncoder(w).Encode(RenderResponse{
				URL:        "https://example.com",
				HTML:       "<html><body>Rendered</body></html>",
				StatusCode: 200,
				Title:      "Example",
			})
			return
		}
		w.WriteHeader(404)
	}))
	defer srv.Close()

	client := NewClient(srv.URL, 5*time.Second)
	result, err := client.Render(context.Background(), "https://example.com")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result.HTML != "<html><body>Rendered</body></html>" {
		t.Errorf("unexpected HTML: %s", result.HTML)
	}
	if result.StatusCode != 200 {
		t.Errorf("expected status 200, got %d", result.StatusCode)
	}
}

func TestClient_Render_Error(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(500)
		w.Write([]byte("internal error"))
	}))
	defer srv.Close()

	client := NewClient(srv.URL, 5*time.Second)
	_, err := client.Render(context.Background(), "https://example.com")
	if err == nil {
		t.Error("expected error")
	}
}

func TestNeedsRendering(t *testing.T) {
	tests := []struct {
		url    string
		expect bool
	}{
		{"https://angular.dev/overview", true},
		{"https://react.dev/learn", true},
		{"https://vuejs.org/guide", true},
		{"https://app.example.com/dashboard", true},
		{"https://en.wikipedia.org/wiki/Go", false},
		{"https://docs.python.org/3/library/json.html", false},
	}

	for _, tt := range tests {
		got := NeedsRendering(tt.url)
		if got != tt.expect {
			t.Errorf("NeedsRendering(%q) = %v, want %v", tt.url, got, tt.expect)
		}
	}
}
