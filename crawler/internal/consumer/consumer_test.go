package consumer

import (
	"context"
	"os"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
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

func TestConsumer_ReadRequests(t *testing.T) {
	rdb := getTestRedis(t)
	defer rdb.Close()
	ctx := context.Background()

	stream := "test:crawl_requests:" + time.Now().Format("150405")
	defer rdb.Del(ctx, stream)

	// Add a crawl request
	rdb.XAdd(ctx, &redis.XAddArgs{
		Stream: stream,
		Values: map[string]any{
			"url":       "https://example.com/test",
			"max_depth": "2",
			"job_id":    "test-job-123",
		},
	})

	c := New(rdb, stream, "test-group", "test-consumer")
	c.Setup(ctx)

	requests, ids, err := c.ReadRequests(ctx, 10, time.Second)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(requests) != 1 {
		t.Fatalf("expected 1 request, got %d", len(requests))
	}
	if requests[0].URL != "https://example.com/test" {
		t.Errorf("expected URL 'https://example.com/test', got %s", requests[0].URL)
	}
	if requests[0].JobID != "test-job-123" {
		t.Errorf("expected job_id 'test-job-123', got %s", requests[0].JobID)
	}

	// Ack
	err = c.Ack(ctx, ids...)
	if err != nil {
		t.Fatalf("ack error: %v", err)
	}
}
