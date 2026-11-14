package config

import (
	"os"
	"strconv"
	"time"
)

type Config struct {
	RedisURL           string
	StreamName         string
	CrawlRequestStream string
	Concurrency        int
	MaxDepth           int
	UserAgent          string
	RequestTimeout     time.Duration
	PolitenessDelay    time.Duration
	StealthMode        bool
	ProxyURL           string
	RendererURL        string
	RendererTimeout    time.Duration
}

func Load() Config {
	return Config{
		RedisURL:           getEnv("REDIS_URL", "redis://localhost:6379"),
		StreamName:         getEnv("REDIS_STREAM_NAME", "phantombot:crawled"),
		CrawlRequestStream: getEnv("CRAWLER_REQUEST_STREAM", "phantombot:crawl_requests"),
		Concurrency:        getEnvInt("CRAWLER_CONCURRENCY", 10),
		MaxDepth:           getEnvInt("CRAWLER_MAX_DEPTH", 3),
		UserAgent:          getEnv("CRAWLER_USER_AGENT", "Phantombot/1.0"),
		RequestTimeout:     time.Duration(getEnvInt("CRAWLER_REQUEST_TIMEOUT_SECONDS", 30)) * time.Second,
		PolitenessDelay:    time.Duration(getEnvInt("CRAWLER_POLITENESS_DELAY_MS", 1000)) * time.Millisecond,
		StealthMode:        getEnvBool("CRAWLER_STEALTH_MODE", false),
		ProxyURL:           getEnv("CRAWLER_PROXY_URL", ""),
		RendererURL:        getEnv("PLAYWRIGHT_URL", "http://localhost:3100"),
		RendererTimeout:    time.Duration(getEnvInt("RENDER_TIMEOUT_MS", 15000)) * time.Millisecond,
	}
}

func getEnv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func getEnvInt(key string, fallback int) int {
	if v := os.Getenv(key); v != "" {
		if i, err := strconv.Atoi(v); err == nil {
			return i
		}
	}
	return fallback
}

func getEnvBool(key string, fallback bool) bool {
	if v := os.Getenv(key); v != "" {
		switch v {
		case "true", "1", "yes":
			return true
		case "false", "0", "no":
			return false
		}
	}
	return fallback
}
