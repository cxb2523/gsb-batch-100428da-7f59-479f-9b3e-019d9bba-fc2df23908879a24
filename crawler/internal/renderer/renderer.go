package renderer

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

type RenderRequest struct {
	URL       string `json:"url"`
	TimeoutMS int    `json:"timeout_ms,omitempty"`
	WaitUntil string `json:"wait_until,omitempty"`
}

type RenderResponse struct {
	URL        string `json:"url"`
	HTML       string `json:"html"`
	StatusCode int    `json:"status_code"`
	Title      string `json:"title"`
}

// Client calls the Playwright render sidecar.
type Client struct {
	baseURL    string
	httpClient *http.Client
}

func NewClient(baseURL string, timeout time.Duration) *Client {
	return &Client{
		baseURL: baseURL,
		httpClient: &http.Client{
			Timeout: timeout,
		},
	}
}

// IsAvailable checks if the renderer service is running.
func (c *Client) IsAvailable(ctx context.Context) bool {
	req, err := http.NewRequestWithContext(ctx, "GET", c.baseURL+"/health", nil)
	if err != nil {
		return false
	}
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return false
	}
	defer resp.Body.Close()
	return resp.StatusCode == 200
}

// Render sends a URL to the Playwright sidecar for JS rendering.
func (c *Client) Render(ctx context.Context, url string) (*RenderResponse, error) {
	reqBody, err := json.Marshal(RenderRequest{
		URL:       url,
		WaitUntil: "networkidle",
	})
	if err != nil {
		return nil, fmt.Errorf("marshaling render request: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, "POST", c.baseURL+"/render", bytes.NewReader(reqBody))
	if err != nil {
		return nil, fmt.Errorf("creating render request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("calling renderer: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("reading renderer response: %w", err)
	}

	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("renderer returned %d: %s", resp.StatusCode, string(body))
	}

	var result RenderResponse
	if err := json.Unmarshal(body, &result); err != nil {
		return nil, fmt.Errorf("parsing renderer response: %w", err)
	}

	return &result, nil
}

// NeedsRendering heuristically checks if a URL likely needs JS rendering.
func NeedsRendering(url string) bool {
	// Common SPA frameworks and patterns
	patterns := []string{
		"angular", "react", "vue",
		"app.", "#/",
		"dashboard", "console",
	}
	for _, p := range patterns {
		if contains(url, p) {
			return true
		}
	}
	return false
}

func contains(s, substr string) bool {
	return len(s) >= len(substr) && (s == substr || len(s) > 0 && containsLower(s, substr))
}

func containsLower(s, substr string) bool {
	for i := 0; i <= len(s)-len(substr); i++ {
		if s[i:i+len(substr)] == substr {
			return true
		}
	}
	return false
}
