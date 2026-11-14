package fetcher

import (
	"fmt"
	"io"
	"net/http"
	"net/url"
	"time"

	"github.com/surya-koritala/phantombot/crawler/pkg/models"
)

// Fetcher performs HTTP requests with configurable user-agent and timeout.
type Fetcher struct {
	client      *http.Client
	userAgent   string
	StealthMode bool
	ProxyURL    string
	uaPool      *UAPool
}

func New(userAgent string, timeout time.Duration) *Fetcher {
	return NewWithOptions(userAgent, timeout, false, "")
}

// NewWithOptions creates a Fetcher with full control over stealth mode and proxy settings.
func NewWithOptions(userAgent string, timeout time.Duration, stealthMode bool, proxyURL string) *Fetcher {
	transport := http.DefaultTransport.(*http.Transport).Clone()

	if proxyURL != "" {
		parsed, err := url.Parse(proxyURL)
		if err == nil {
			transport.Proxy = http.ProxyURL(parsed)
		}
	}

	return &Fetcher{
		client: &http.Client{
			Timeout: timeout,
			Transport: transport,
			CheckRedirect: func(req *http.Request, via []*http.Request) error {
				if len(via) >= 10 {
					return fmt.Errorf("too many redirects (max 10)")
				}
				return nil
			},
		},
		userAgent:   userAgent,
		StealthMode: stealthMode,
		ProxyURL:    proxyURL,
		uaPool:      NewUAPool(nil),
	}
}

// Fetch retrieves a URL and returns a CrawlResult.
// HTTP error status codes (4xx, 5xx) are returned in the result, not as Go errors.
// Go errors are returned only for network/transport failures.
func (f *Fetcher) Fetch(rawURL string, depth int, jobID string) (*models.CrawlResult, error) {
	req, err := http.NewRequest("GET", rawURL, nil)
	if err != nil {
		return nil, fmt.Errorf("creating request for %q: %w", rawURL, err)
	}

	if f.StealthMode {
		req.Header.Set("User-Agent", f.uaPool.Next())
		req.Header.Set("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8")
		req.Header.Set("Accept-Language", "en-US,en;q=0.9")
		req.Header.Set("Accept-Encoding", "gzip, deflate, br")
		req.Header.Set("Connection", "keep-alive")
		req.Header.Set("Sec-Fetch-Dest", "document")
		req.Header.Set("Sec-Fetch-Mode", "navigate")
		req.Header.Set("Sec-Fetch-Site", "none")
	} else {
		req.Header.Set("User-Agent", f.userAgent)
	}

	resp, err := f.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("fetching %q: %w", rawURL, err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("reading body from %q: %w", rawURL, err)
	}

	headers := make(map[string]string)
	for k := range resp.Header {
		headers[k] = resp.Header.Get(k)
	}

	return &models.CrawlResult{
		URL:         rawURL,
		StatusCode:  resp.StatusCode,
		ContentType: resp.Header.Get("Content-Type"),
		HTML:        string(body),
		Headers:     headers,
		FetchedAt:   time.Now().UTC(),
		Rendered:    false,
		Depth:       depth,
		JobID:       jobID,
	}, nil
}
