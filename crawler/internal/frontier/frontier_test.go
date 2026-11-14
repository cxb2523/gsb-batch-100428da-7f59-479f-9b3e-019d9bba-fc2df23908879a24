package frontier

import (
	"testing"
	"time"
)

func TestFrontier_Push_Pop(t *testing.T) {
	f := New(time.Millisecond * 100)

	f.Push("https://example.com/page1", 0)
	f.Push("https://example.com/page2", 0)

	url, depth, ok := f.Pop()
	if !ok {
		t.Fatal("expected to pop a URL")
	}
	if url != "https://example.com/page1" {
		t.Errorf("expected https://example.com/page1, got %s", url)
	}
	if depth != 0 {
		t.Errorf("expected depth 0, got %d", depth)
	}

	url, _, ok = f.Pop()
	if !ok {
		t.Fatal("expected to pop a URL")
	}
	if url != "https://example.com/page2" {
		t.Errorf("expected https://example.com/page2, got %s", url)
	}
}

func TestFrontier_Dedup(t *testing.T) {
	f := New(time.Millisecond * 100)

	f.Push("https://example.com/page1", 0)
	f.Push("https://example.com/page1", 0) // duplicate

	url, _, ok := f.Pop()
	if !ok || url != "https://example.com/page1" {
		t.Fatal("expected first URL")
	}

	_, _, ok = f.Pop()
	if ok {
		t.Fatal("expected no more URLs — duplicate should have been skipped")
	}
}

func TestFrontier_Empty(t *testing.T) {
	f := New(time.Millisecond * 100)

	_, _, ok := f.Pop()
	if ok {
		t.Fatal("expected empty frontier to return ok=false")
	}
}

func TestFrontier_PolitenessDelay(t *testing.T) {
	delay := time.Millisecond * 200
	f := New(delay)

	f.Push("https://example.com/page1", 0)
	f.Push("https://example.com/page2", 0) // same domain

	_, _, _ = f.Pop()
	start := time.Now()
	_, _, ok := f.Pop()
	elapsed := time.Since(start)

	if !ok {
		t.Fatal("expected to pop second URL")
	}
	if elapsed < delay/2 {
		t.Errorf("expected politeness delay of ~%v, but popped in %v", delay, elapsed)
	}
}

func TestFrontier_Len(t *testing.T) {
	f := New(time.Millisecond * 100)

	if f.Len() != 0 {
		t.Errorf("expected len 0, got %d", f.Len())
	}

	f.Push("https://example.com/a", 0)
	f.Push("https://example.com/b", 1)

	if f.Len() != 2 {
		t.Errorf("expected len 2, got %d", f.Len())
	}
}
