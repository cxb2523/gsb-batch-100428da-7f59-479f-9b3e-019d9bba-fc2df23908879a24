package frontier

import (
	"net/url"
	"sync"
	"time"
)

type entry struct {
	url   string
	depth int
}

// Frontier is a thread-safe URL queue with deduplication and per-domain politeness.
type Frontier struct {
	mu              sync.Mutex
	queue           []entry
	seen            map[string]bool
	lastFetchByHost map[string]time.Time
	politenessDelay time.Duration
}

func New(politenessDelay time.Duration) *Frontier {
	return &Frontier{
		queue:           make([]entry, 0),
		seen:            make(map[string]bool),
		lastFetchByHost: make(map[string]time.Time),
		politenessDelay: politenessDelay,
	}
}

// Push adds a URL to the frontier if it hasn't been seen before.
func (f *Frontier) Push(rawURL string, depth int) {
	f.mu.Lock()
	defer f.mu.Unlock()

	if f.seen[rawURL] {
		return
	}
	f.seen[rawURL] = true
	f.queue = append(f.queue, entry{url: rawURL, depth: depth})
}

// Pop returns the next URL, respecting per-domain politeness delays.
// Returns ("", 0, false) if the frontier is empty.
func (f *Frontier) Pop() (string, int, bool) {
	f.mu.Lock()
	if len(f.queue) == 0 {
		f.mu.Unlock()
		return "", 0, false
	}

	e := f.queue[0]
	f.queue = f.queue[1:]

	host := extractHost(e.url)
	lastFetch, exists := f.lastFetchByHost[host]
	f.mu.Unlock()

	if exists {
		elapsed := time.Since(lastFetch)
		if elapsed < f.politenessDelay {
			time.Sleep(f.politenessDelay - elapsed)
		}
	}

	f.mu.Lock()
	f.lastFetchByHost[host] = time.Now()
	f.mu.Unlock()

	return e.url, e.depth, true
}

// Len returns the number of URLs waiting in the queue.
func (f *Frontier) Len() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.queue)
}

func extractHost(rawURL string) string {
	u, err := url.Parse(rawURL)
	if err != nil {
		return rawURL
	}
	return u.Host
}
