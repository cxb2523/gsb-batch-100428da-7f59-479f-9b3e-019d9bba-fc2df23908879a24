package consumer

import (
	"context"
	"time"

	"github.com/redis/go-redis/v9"
)

// CrawlRequest from the API
type CrawlRequest struct {
	URL      string
	MaxDepth int
	JobID    string
}

// Consumer reads crawl requests from a Redis Stream
type Consumer struct {
	rdb    *redis.Client
	stream string
	group  string
	name   string
}

func New(rdb *redis.Client, stream, group, name string) *Consumer {
	return &Consumer{rdb: rdb, stream: stream, group: group, name: name}
}

func (c *Consumer) Setup(ctx context.Context) error {
	err := c.rdb.XGroupCreateMkStream(ctx, c.stream, c.group, "0").Err()
	if err != nil && err.Error() != "BUSYGROUP Consumer Group name already exists" {
		return err
	}
	return nil
}

// ReadRequests blocks for up to blockTime waiting for crawl requests.
// Returns a slice of CrawlRequest and their message IDs for acknowledgement.
func (c *Consumer) ReadRequests(ctx context.Context, count int64, blockTime time.Duration) ([]CrawlRequest, []string, error) {
	streams, err := c.rdb.XReadGroup(ctx, &redis.XReadGroupArgs{
		Group:    c.group,
		Consumer: c.name,
		Streams:  []string{c.stream, ">"},
		Count:    count,
		Block:    blockTime,
	}).Result()

	if err == redis.Nil || len(streams) == 0 {
		return nil, nil, nil
	}
	if err != nil {
		return nil, nil, err
	}

	var requests []CrawlRequest
	var ids []string

	for _, stream := range streams {
		for _, msg := range stream.Messages {
			maxDepth := 3
			if md, ok := msg.Values["max_depth"]; ok {
				if v, ok := md.(string); ok {
					for _, c := range v {
						if c >= '0' && c <= '9' {
							maxDepth = int(c - '0')
						}
					}
				}
			}

			url, _ := msg.Values["url"].(string)
			jobID, _ := msg.Values["job_id"].(string)

			if url != "" {
				requests = append(requests, CrawlRequest{
					URL:      url,
					MaxDepth: maxDepth,
					JobID:    jobID,
				})
				ids = append(ids, msg.ID)
			}
		}
	}

	return requests, ids, nil
}

func (c *Consumer) Ack(ctx context.Context, ids ...string) error {
	return c.rdb.XAck(ctx, c.stream, c.group, ids...).Err()
}
