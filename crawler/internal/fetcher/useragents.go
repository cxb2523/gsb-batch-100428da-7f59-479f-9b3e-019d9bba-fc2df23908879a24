package fetcher

import (
	"math/rand"
	"sync"
)

// Common browser user-agents for rotation
var defaultUserAgents = []string{
	"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
	"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
	"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
	"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
	"Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:133.0) Gecko/20100101 Firefox/133.0",
	"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
	"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
}

// UAPool provides rotating user-agent selection.
type UAPool struct {
	mu     sync.Mutex
	agents []string
}

func NewUAPool(agents []string) *UAPool {
	if len(agents) == 0 {
		agents = defaultUserAgents
	}
	return &UAPool{agents: agents}
}

// Next returns a random user-agent from the pool.
func (p *UAPool) Next() string {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.agents[rand.Intn(len(p.agents))]
}

// Len returns the number of agents in the pool.
func (p *UAPool) Len() int {
	return len(p.agents)
}
