package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"net/url"
	"os"
	"os/signal"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/redis/go-redis/v9"
	"github.com/surya-koritala/phantombot/crawler/internal/config"
	"github.com/surya-koritala/phantombot/crawler/internal/consumer"
	"github.com/surya-koritala/phantombot/crawler/internal/fetcher"
	"github.com/surya-koritala/phantombot/crawler/internal/frontier"
	"github.com/surya-koritala/phantombot/crawler/internal/publisher"
	"github.com/surya-koritala/phantombot/crawler/internal/renderer"
	"github.com/surya-koritala/phantombot/crawler/internal/robots"
	"github.com/surya-koritala/phantombot/crawler/pkg/models"
)

func main() {
	seedURLs := flag.String("urls", "", "Comma-separated seed URLs to crawl")
	listen := flag.Bool("listen", false, "Run in server mode: continuously read crawl requests from Redis stream")
	flag.Parse()

	if !*listen && *seedURLs == "" {
		fmt.Fprintln(os.Stderr, "usage: phantombot -urls https://example.com,https://other.com")
		fmt.Fprintln(os.Stderr, "       phantombot -listen")
		os.Exit(1)
	}

	cfg := config.Load()

	opts, err := redis.ParseURL(cfg.RedisURL)
	if err != nil {
		log.Fatalf("invalid REDIS_URL: %v", err)
	}
	rdb := redis.NewClient(opts)
	defer rdb.Close()

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	if err := rdb.Ping(ctx).Err(); err != nil {
		log.Fatalf("cannot connect to Redis: %v", err)
	}

	// Handle graceful shutdown
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		<-sigCh
		log.Println("shutting down...")
		cancel()
	}()

	f := frontier.New(cfg.PolitenessDelay)
	fetch := fetcher.NewWithOptions(cfg.UserAgent, cfg.RequestTimeout, cfg.StealthMode, cfg.ProxyURL)
	pub := publisher.New(rdb, cfg.StreamName)
	robo := robots.NewChecker(cfg.UserAgent)
	renderFallback := newRenderFallback(ctx, cfg)

	if *listen {
		runServerMode(ctx, cfg, rdb, f, fetch, pub, robo, renderFallback)
	} else {
		runCLIMode(ctx, cfg, *seedURLs, f, fetch, pub, robo, renderFallback)
	}
}

// runCLIMode crawls the given seed URLs and exits when done — the original behaviour.
func runCLIMode(
	ctx context.Context,
	cfg config.Config,
	seedURLs string,
	f *frontier.Frontier,
	fetch *fetcher.Fetcher,
	pub *publisher.Publisher,
	robo *robots.Checker,
	renderFallback func(context.Context, *models.CrawlResult, string),
) {
	for raw := range strings.SplitSeq(seedURLs, ",") {
		raw = strings.TrimSpace(raw)
		if raw != "" {
			f.Push(raw, 0)
		}
	}

	log.Printf("phantombot starting: %d seed URLs, concurrency=%d, max_depth=%d",
		f.Len(), cfg.Concurrency, cfg.MaxDepth)

	runWorkers(ctx, cfg, f, fetch, pub, robo, renderFallback)
	log.Println("crawl complete")
}

// runServerMode continuously reads crawl requests from the Redis stream and crawls them.
func runServerMode(
	ctx context.Context,
	cfg config.Config,
	rdb *redis.Client,
	f *frontier.Frontier,
	fetch *fetcher.Fetcher,
	pub *publisher.Publisher,
	robo *robots.Checker,
	renderFallback func(context.Context, *models.CrawlResult, string),
) {
	log.Printf("phantombot listening on stream %s (concurrency=%d, max_depth=%d)",
		cfg.CrawlRequestStream, cfg.Concurrency, cfg.MaxDepth)

	cons := consumer.New(rdb, cfg.CrawlRequestStream, "phantombot-crawlers", "phantombot-1")
	if err := cons.Setup(ctx); err != nil {
		log.Fatalf("consumer setup failed: %v", err)
	}

	// Start worker pool — workers block on frontier.Pop() until context is cancelled.
	var wg sync.WaitGroup
	for i := 0; i < cfg.Concurrency; i++ {
		wg.Add(1)
		go func(workerID int) {
			defer wg.Done()
			workerLoop(ctx, workerID, cfg, f, fetch, pub, robo, renderFallback)
		}(i)
	}

	// Main loop: poll the stream and push URLs into the frontier.
	for {
		if ctx.Err() != nil {
			break
		}

		requests, ids, err := cons.ReadRequests(ctx, 10, 2*time.Second)
		if err != nil {
			if ctx.Err() != nil {
				break
			}
			log.Printf("stream read error: %v", err)
			continue
		}

		for _, req := range requests {
			log.Printf("queuing crawl job=%s url=%s max_depth=%d", req.JobID, req.URL, req.MaxDepth)
			f.Push(req.URL, 0)
		}

		if len(ids) > 0 {
			if err := cons.Ack(ctx, ids...); err != nil {
				log.Printf("ack error: %v", err)
			}
		}
	}

	wg.Wait()
	log.Println("server mode shut down")
}

// runWorkers starts the worker pool and waits for it to finish (CLI mode).
func runWorkers(
	ctx context.Context,
	cfg config.Config,
	f *frontier.Frontier,
	fetch *fetcher.Fetcher,
	pub *publisher.Publisher,
	robo *robots.Checker,
	renderFallback func(context.Context, *models.CrawlResult, string),
) {
	var wg sync.WaitGroup
	for i := 0; i < cfg.Concurrency; i++ {
		wg.Add(1)
		go func(workerID int) {
			defer wg.Done()
			workerLoop(ctx, workerID, cfg, f, fetch, pub, robo, renderFallback)
		}(i)
	}
	wg.Wait()
}

// workerLoop is the shared fetch-publish loop used by both modes.
// Smart fallback: tries HTTP first, falls back to Playwright renderer if the
// response is blocked (403/401), has sparse content, or the URL looks JS-heavy.
// The render outcome is always recorded on the published result; a failed
// render keeps the first fetch's HTML and status code.
func workerLoop(
	ctx context.Context,
	workerID int,
	cfg config.Config,
	f *frontier.Frontier,
	fetch *fetcher.Fetcher,
	pub *publisher.Publisher,
	robo *robots.Checker,
	fallback func(context.Context, *models.CrawlResult, string),
) {
	for {
		if ctx.Err() != nil {
			return
		}

		rawURL, depth, ok := f.Pop()
		if !ok {
			return
		}

		allowed, err := robo.IsAllowed(rawURL)
		if err != nil {
			log.Printf("[worker %d] robots check error for %s: %v", workerID, rawURL, err)
		}
		if !allowed {
			log.Printf("[worker %d] blocked by robots.txt: %s", workerID, rawURL)
			continue
		}

		log.Printf("[worker %d] fetching: %s (depth=%d)", workerID, rawURL, depth)
		result, err := fetchAndRender(ctx, fetch, fallback, rawURL, depth)
		if err != nil {
			log.Printf("[worker %d] fetch error for %s: %v", workerID, rawURL, err)
			continue
		}

		if err := pub.Publish(ctx, result); err != nil {
			log.Printf("[worker %d] publish error for %s: %v", workerID, rawURL, err)
			continue
		}

		log.Printf("[worker %d] published: %s (status=%d, rendered=%v, render_status=%s)",
			workerID, rawURL, result.StatusCode, result.Rendered, result.RenderStatus)

		// Extract links and add to frontier if within depth limit
		if depth < cfg.MaxDepth {
			links := extractLinks(result.HTML, rawURL)
			for _, link := range links {
				f.Push(link, depth+1)
			}
		}
	}
}

// fetchAndRender performs the initial HTTP fetch and, when the result looks
// blocked or sparse, applies the render fallback. The fallback records the
// render outcome and never overwrites the first fetch's HTML/status on failure.
func fetchAndRender(
	ctx context.Context,
	fetch *fetcher.Fetcher,
	fallback func(context.Context, *models.CrawlResult, string),
	rawURL string,
	depth int,
) (*models.CrawlResult, error) {
	result, err := fetch.Fetch(rawURL, depth, "")
	if err != nil {
		return nil, err
	}
	if fallback != nil {
		fallback(ctx, result, rawURL)
	}
	return result, nil
}

// newRenderFallback builds the render fallback shared by all workers, or nil
// when no renderer URL is configured (the plain HTTP path is then unchanged:
// no render is attempted and no render markers are published). The renderer
// availability is probed once here (and again only after an outage cooldown)
// rather than per page.
func newRenderFallback(ctx context.Context, cfg config.Config) func(context.Context, *models.CrawlResult, string) {
	if cfg.RendererURL == "" {
		log.Printf("no Playwright renderer configured; pages are published from the plain HTTP fetch")
		return nil
	}

	client := renderer.NewClient(cfg.RendererURL, cfg.RendererTimeout)
	fb := renderer.NewFallback(client, cfg.RendererCooldown)

	probeCtx, cancel := context.WithTimeout(ctx, probeTimeout(cfg.RendererTimeout))
	defer cancel()
	if fb.ProbeAvailability(probeCtx) {
		log.Printf("Playwright renderer available at %s", cfg.RendererURL)
	} else {
		log.Printf("Playwright renderer unavailable at %s; pages will be published from the plain HTTP fetch", cfg.RendererURL)
	}

	return fb.Apply
}

// probeTimeout bounds the start-up health check so an unreachable renderer
// cannot delay crawling for longer than its own request timeout.
func probeTimeout(rendererTimeout time.Duration) time.Duration {
	const maxProbe = 5 * time.Second
	if rendererTimeout <= 0 || rendererTimeout > maxProbe {
		return maxProbe
	}
	return rendererTimeout
}

// extractLinks does basic <a href="..."> extraction.
func extractLinks(html, baseURL string) []string {
	var links []string
	base, err := url.Parse(baseURL)
	if err != nil {
		return links
	}

	// Simple extraction — find href="..." patterns
	remaining := html
	for {
		idx := strings.Index(remaining, "href=\"")
		if idx == -1 {
			break
		}
		remaining = remaining[idx+6:]
		end := strings.Index(remaining, "\"")
		if end == -1 {
			break
		}
		href := remaining[:end]
		remaining = remaining[end:]

		parsed, err := url.Parse(href)
		if err != nil {
			continue
		}
		resolved := base.ResolveReference(parsed)

		// Only follow http/https links
		if resolved.Scheme == "http" || resolved.Scheme == "https" {
			links = append(links, resolved.String())
		}
	}
	return links
}
