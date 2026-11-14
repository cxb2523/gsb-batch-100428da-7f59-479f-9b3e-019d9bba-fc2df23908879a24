package publisher

import (
	"context"
	"encoding/json"
	"fmt"
	"strconv"

	"github.com/redis/go-redis/v9"
	"github.com/surya-koritala/phantombot/crawler/pkg/models"
)

// Publisher writes CrawlResults to a Redis Stream.
type Publisher struct {
	rdb    *redis.Client
	stream string
}

func New(rdb *redis.Client, stream string) *Publisher {
	return &Publisher{rdb: rdb, stream: stream}
}

func (p *Publisher) Publish(ctx context.Context, result *models.CrawlResult) error {
	values, err := MessageValues(result)
	if err != nil {
		return err
	}

	err = p.rdb.XAdd(ctx, &redis.XAddArgs{
		Stream: p.stream,
		Values: values,
	}).Err()
	if err != nil {
		return fmt.Errorf("publishing to stream %q: %w", p.stream, err)
	}
	return nil
}

// MessageValues builds the Redis Streams field map for a crawl result. It is
// exported so the wire format can be verified without a Redis server.
func MessageValues(result *models.CrawlResult) (map[string]any, error) {
	headersJSON, err := json.Marshal(result.Headers)
	if err != nil {
		return nil, fmt.Errorf("marshaling headers: %w", err)
	}

	values := map[string]any{
		"url":           result.URL,
		"status_code":   strconv.Itoa(result.StatusCode),
		"content_type":  result.ContentType,
		"html":          result.HTML,
		"headers":       string(headersJSON),
		"fetched_at":    result.FetchedAt.Format("2006-01-02T15:04:05Z"),
		"rendered":      strconv.FormatBool(result.Rendered),
		"render_status": result.RenderStatus,
		"render_reason": result.RenderReason,
		"title":         result.Title,
		"depth":         strconv.Itoa(result.Depth),
	}
	if result.JobID != "" {
		values["job_id"] = result.JobID
	}
	return values, nil
}
