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

	if *listen {
		runServerMode(ctx, cfg, rdb, f, fetch, pub, robo)
	} else {
		runCLIMode(ctx, cfg, *seedURLs, f, fetch, pub, robo)
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
) {
	for raw := range strings.SplitSeq(seedURLs, ",") {
		raw = strings.TrimSpace(raw)
		if raw != "" {
			f.Push(raw, 0)
		}
	}

	log.Printf("phantombot starting: %d seed URLs, concurrency=%d, max_depth=%d",
		f.Len(), cfg.Concurrency, cfg.MaxDepth)

	runWorkers(ctx, cfg, f, fetch, pub, robo)
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
			workerLoop(ctx, workerID, cfg, f, fetch, pub, robo)
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
) {
	var wg sync.WaitGroup
	for i := 0; i < cfg.Concurrency; i++ {
		wg.Add(1)
		go func(workerID int) {
			defer wg.Done()
			workerLoop(ctx, workerID, cfg, f, fetch, pub, robo)
		}(i)
	}
	wg.Wait()
}

// workerLoop is the shared fetch-publish loop used by both modes.
// Smart fallback: tries HTTP first, falls back to Playwright renderer if the
// response is blocked (403/401), has sparse content, or the URL looks JS-heavy.
func workerLoop(
	ctx context.Context,
	workerID int,
	cfg config.Config,
	f *frontier.Frontier,
	fetch *fetcher.Fetcher,
	pub *publisher.Publisher,
	robo *robots.Checker,
) {
	// Create renderer client (may not be available)
	render := renderer.NewClient(cfg.RendererURL, cfg.RendererTimeout)
	rendererAvailable := render.IsAvailable(ctx)
	if rendererAvailable {
		log.Printf("[worker %d] Playwright renderer available at %s", workerID, cfg.RendererURL)
	}

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
		result, err := fetch.Fetch(rawURL, depth, "")
		if err != nil {
			log.Printf("[worker %d] fetch error for %s: %v", workerID, rawURL, err)
			continue
		}

		// Smart fallback: retry with Playwright if HTTP result is poor
		if rendererAvailable && needsRendering(result, rawURL) {
			log.Printf("[worker %d] sparse/blocked content, retrying with Playwright: %s", workerID, rawURL)
			rendered, renderErr := render.Render(ctx, rawURL)
			if renderErr == nil {
				result.HTML = rendered.HTML
				result.StatusCode = rendered.StatusCode
				result.Rendered = true
				log.Printf("[worker %d] Playwright rendered: %s (status=%d)", workerID, rawURL, rendered.StatusCode)
			} else {
				log.Printf("[worker %d] Playwright render failed for %s: %v", workerID, rawURL, renderErr)
			}
		}

		if err := pub.Publish(ctx, result); err != nil {
			log.Printf("[worker %d] publish error for %s: %v", workerID, rawURL, err)
			continue
		}

		log.Printf("[worker %d] published: %s (status=%d, rendered=%v)", workerID, rawURL, result.StatusCode, result.Rendered)

		// Extract links and add to frontier if within depth limit
		if depth < cfg.MaxDepth {
			links := extractLinks(result.HTML, rawURL)
			for _, link := range links {
				f.Push(link, depth+1)
			}
		}
	}
}

// needsRendering decides if a fetch result should be retried with Playwright.
func needsRendering(result *models.CrawlResult, rawURL string) bool {
	// Blocked by anti-bot
	if result.StatusCode == 403 || result.StatusCode == 401 {
		return true
	}
	// Very little content (likely JS-rendered page)
	if result.StatusCode == 200 && len(result.HTML) < 1000 {
		return true
	}
	// URL heuristics suggest SPA
	if renderer.NeedsRendering(rawURL) {
		return true
	}
	return false
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
