package embed

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"time"
)

type Client struct {
	baseUrl     string
	model       string
	queryPrefix string
	http        *http.Client
}

func NewClient(baseUrl, model, queryPrefix string) *Client {
	return &Client{
		baseUrl:     baseUrl,
		model:       model,
		queryPrefix: queryPrefix,
		http:        &http.Client{Timeout: 60 * time.Second},
	}
}

type embedRequest struct {
	Model string   `json:"model"`
	Input []string `json:"input"`
}

type embedResponse struct {
	Embeddings [][]float32 `json:"embeddings"`
}

func (c *Client) EmbedQuery(ctx context.Context, query string) ([]float32, error) {

	// Construct JSON request body
	body, err := json.Marshal(embedRequest{
		Model: c.model,
		Input: []string{c.queryPrefix + query},
	})
	if err != nil {
		return nil, fmt.Errorf("marshal request: %w", err)
	}

	// Construct the HTTP request
	url := c.baseUrl + "/api/embed"
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("build request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")

	// Send HTTP request to Ollama
	resp, err := c.http.Do(req)
	if err != nil {
		return nil, fmt.Errorf("call ollama: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("ollama returned %s", resp.Status)
	}

	// Parse response
	var out embedResponse
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return nil, fmt.Errorf("decode response: %w", err)
	}

	if len(out.Embeddings) != 1 {
		return nil, fmt.Errorf("expected 1 embedding, got %d", len(out.Embeddings))
	}

	return out.Embeddings[0], nil
}
