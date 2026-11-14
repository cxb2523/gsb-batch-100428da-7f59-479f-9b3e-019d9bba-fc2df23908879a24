package publisher

import (
	"context"
	"os"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
	"github.com/surya-koritala/phantombot/crawler/pkg/models"
)

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
