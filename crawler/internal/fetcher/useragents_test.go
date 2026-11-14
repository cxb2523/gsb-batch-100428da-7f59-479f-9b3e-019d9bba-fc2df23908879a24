package fetcher

import (
	"testing"
)

func TestUAPool_Next(t *testing.T) {
	pool := NewUAPool(nil)
	ua := pool.Next()
	if ua == "" {
		t.Error("expected non-empty user agent")
	}
	if len(ua) < 20 {
		t.Errorf("user agent seems too short: %s", ua)
	}
}

func TestUAPool_Len(t *testing.T) {
	pool := NewUAPool(nil)
	if pool.Len() == 0 {
		t.Error("expected default pool to have agents")
	}
}

func TestUAPool_CustomAgents(t *testing.T) {
	custom := []string{"Agent/1.0", "Agent/2.0"}
	pool := NewUAPool(custom)
	if pool.Len() != 2 {
		t.Errorf("expected 2 agents, got %d", pool.Len())
	}
	ua := pool.Next()
	if ua != "Agent/1.0" && ua != "Agent/2.0" {
		t.Errorf("unexpected agent: %s", ua)
	}
}

func TestUAPool_Rotates(t *testing.T) {
	pool := NewUAPool(nil)
	seen := make(map[string]bool)
	for i := 0; i < 100; i++ {
		seen[pool.Next()] = true
	}
	// With 7 agents and 100 draws, we should see at least 3 different ones
	if len(seen) < 3 {
		t.Errorf("expected rotation, only saw %d unique agents", len(seen))
	}
}
