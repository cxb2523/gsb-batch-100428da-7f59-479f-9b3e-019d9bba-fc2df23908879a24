package robots

import (
	"fmt"
	"io"
	"net/http"
	"net/url"
	"sync"
	"time"

	"github.com/temoto/robotstxt"
)

// Checker fetches and caches robots.txt per host, then checks if a URL is allowed.
type Checker struct {
	mu        sync.RWMutex
	cache     map[string]*robotstxt.RobotsData
	userAgent string
	client    *http.Client
}

func NewChecker(userAgent string) *Checker {
	return &Checker{
		cache:     make(map[string]*robotstxt.RobotsData),
		userAgent: userAgent,
		client: &http.Client{
			Timeout: 10 * time.Second,
		},
	}
}

func (c *Checker) IsAllowed(rawURL string) (bool, error) {
	u, err := url.Parse(rawURL)
	if err != nil {
		return false, fmt.Errorf("invalid URL %q: %w", rawURL, err)
	}

	host := u.Host
	robots, err := c.getRobots(u.Scheme, host)
	if err != nil {
		// If we can't fetch robots.txt, allow by default
		return true, nil
	}
	if robots == nil {
		return true, nil
	}

	return robots.TestAgent(u.Path, c.userAgent), nil
}

func (c *Checker) getRobots(scheme, host string) (*robotstxt.RobotsData, error) {
	c.mu.RLock()
	if robots, ok := c.cache[host]; ok {
		c.mu.RUnlock()
		return robots, nil
	}
	c.mu.RUnlock()

	robotsURL := fmt.Sprintf("%s://%s/robots.txt", scheme, host)
	resp, err := c.client.Get(robotsURL)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		c.mu.Lock()
		c.cache[host] = nil
		c.mu.Unlock()
		return nil, nil
	}

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}

	robots, err := robotstxt.FromBytes(body)
	if err != nil {
		return nil, err
	}

	c.mu.Lock()
	c.cache[host] = robots
	c.mu.Unlock()

	return robots, nil
}
