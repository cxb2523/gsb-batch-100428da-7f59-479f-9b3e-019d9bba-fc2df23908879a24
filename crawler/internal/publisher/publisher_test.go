package publisher

import (
	"context"
	"encoding/json"
	"os"
	"strconv"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
	"github.com/surya-koritala/phantombot/crawler/pkg/models"
)

func TestMessageValues_RenderMarkers(t *testing.T) {
	ts := time.Date(2026, 4, 12, 12, 0, 0, 0, time.UTC)

	tests := []struct {
		name         string
		result       *models.CrawlResult
		wantRendered string
		wantStatus   string
		wantTitle    string
	}{
		{
			name: "not attempted",
			result: &models.CrawlResult{
				URL: "https://example.com", StatusCode: 200, ContentType: "text/html",
				HTML: "<html></html>", Headers: map[string]string{}, FetchedAt: ts,
			},
			wantRendered: "false",
			wantStatus:   "",
		},
		{
			name: "render succeeded",
			result: &models.CrawlResult{
				URL: "https://example.com", StatusCode: 200, ContentType: "text/html; charset=utf-8",
				HTML: "<html><title>Rendered</title></html>", Headers: map[string]string{},
				FetchedAt: ts, Rendered: true, Title: "Rendered",
				RenderStatus: models.RenderStatusSuccess, RenderReason: "rendered by playwright sidecar",
			},
			wantRendered: "true",
			wantStatus:   models.RenderStatusSuccess,
			wantTitle:    "Rendered",
		},
		{
			name: "render timed out keeps first fetch",
			result: &models.CrawlResult{
				URL: "https://example.com", StatusCode: 403, ContentType: "text/html",
				HTML: "<html><body>denied</body></html>", Headers: map[string]string{},
				FetchedAt: ts, Rendered: false,
				RenderStatus: models.RenderStatusTimeout, RenderReason: "render request timed out",
			},
			wantRendered: "false",
			wantStatus:   models.RenderStatusTimeout,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			values, err := MessageValues(tt.result)
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if got := values["rendered"]; got != tt.wantRendered {
				t.Errorf("rendered = %v, want %q", got, tt.wantRendered)
			}
			if got := values["render_status"]; got != tt.wantStatus {
				t.Errorf("render_status = %v, want %q", got, tt.wantStatus)
			}
			if got := values["title"]; got != tt.wantTitle {
				t.Errorf("title = %v, want %q", got, tt.wantTitle)
			}
			if _, ok := values["render_reason"]; !ok {
				t.Error("expected render_reason field to always be published")
			}
			if values["status_code"] != strconv.Itoa(tt.result.StatusCode) {
				t.Errorf("status_code = %v, want %d", values["status_code"], tt.result.StatusCode)
			}
			// Headers must remain valid JSON.
			var headers map[string]string
			if err := json.Unmarshal([]byte(values["headers"].(string)), &headers); err != nil {
				t.Errorf("headers is not valid JSON: %v", err)
			}
		})
	}
}

func getTestRedis(t *testing.T) *redis.Client {
	t.Helper()
	url := os.Getenv("REDIS_URL")
	if url == "" {
		url = "redis://localhost:6379"
	}
	opts, err := redis.ParseURL(url)
	if err != nil {
		t.Skipf("skipping: invalid REDIS_URL: %v", err)
	}
	client := redis.NewClient(opts)
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	if err := client.Ping(ctx).Err(); err != nil {
		t.Skipf("skipping: redis not available: %v", err)
	}
	return client
}

func TestPublisher_Publish(t *testing.T) {
	rdb := getTestRedis(t)
	defer rdb.Close()

	ctx := context.Background()
	stream := "test:phantombot:crawled:" + time.Now().Format("150405")
	defer rdb.Del(ctx, stream)

	p := New(rdb, stream)

	result := &models.CrawlResult{
		URL:         "https://example.com/page",
		StatusCode:  200,
		ContentType: "text/html",
		HTML:        "<html><body>test</body></html>",
		Headers:     map[string]string{"Server": "nginx"},
		FetchedAt:   time.Date(2026, 4, 12, 12, 0, 0, 0, time.UTC),
		Rendered:    false,
		Depth:       1,
	}

	err := p.Publish(ctx, result)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	// Read it back from the stream
	msgs, err := rdb.XRange(ctx, stream, "-", "+").Result()
	if err != nil {
		t.Fatalf("reading stream: %v", err)
	}
	if len(msgs) != 1 {
		t.Fatalf("expected 1 message, got %d", len(msgs))
	}

	msg := msgs[0].Values
	if msg["url"] != "https://example.com/page" {
		t.Errorf("expected url 'https://example.com/page', got %v", msg["url"])
	}
	if msg["status_code"] != "200" {
		t.Errorf("expected status_code '200', got %v", msg["status_code"])
	}
	if msg["rendered"] != "false" {
		t.Errorf("expected rendered 'false', got %v", msg["rendered"])
	}
}
