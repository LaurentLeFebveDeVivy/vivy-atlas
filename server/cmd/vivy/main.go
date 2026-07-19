package main

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/LaurentLeFebveDeVivy/vivy-atlas/server/internal/config"
	"github.com/LaurentLeFebveDeVivy/vivy-atlas/server/internal/embed"
	"github.com/LaurentLeFebveDeVivy/vivy-atlas/server/internal/search"
	"github.com/LaurentLeFebveDeVivy/vivy-atlas/server/internal/store"
	"github.com/jackc/pgx/v5"
	"github.com/spf13/cobra"
)

func main() {

	// Define top-level command "vivy"
	root := &cobra.Command{
		Use:          "vivy",
		Short:        "VivyAtlas - personal memory search",
		SilenceUsage: true,
	}
	// Attach search command with flags. Now: "vivy search 'query' --limit N"
	root.AddCommand(newSearchCmd())

	// Execute the command
	if err := root.Execute(); err != nil {
		os.Exit(1)
	}
}

func newSearchCmd() *cobra.Command {
	var limit int
	cmd := &cobra.Command{
		Use:   `search "your query"`,
		Short: "Hybrid search over indexed chunks",
		Args:  cobra.ExactArgs(1), // One exact argument for the query text
		RunE: func(cmd *cobra.Command, args []string) error { //Function that is executed when "vivy search" is invoked
			return runSearch(cmd.Context(), args[0], limit)
		},
	}
	cmd.Flags().IntVarP(&limit, "limit", "l", 10, "max results") // optional limit for number of search results, default 10
	return cmd
}

func runSearch(ctx context.Context, query string, limit int) error {

	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()

	// Load config
	home, err := os.UserHomeDir()
	if err != nil {
		return err
	}

	path := filepath.Join(home, ".config", "vivyatlas", "config.yaml")

	cfg, err := config.Load(path)
	if err != nil {
		return err
	}

	// Initialize Ollama client and embed the query
	client := embed.NewClient(cfg.Embedding.BaseURL, cfg.Embedding.Model, cfg.Embedding.QueryPrefix)
	vec, err := client.EmbedQuery(ctx, query)
	if err != nil {
		return err
	}

	// Open the connection to postgres
	// Single connection. Fine for CLI. Replace later with connection pool (for API)
	conn, err := pgx.Connect(ctx, cfg.Database.URL)
	if err != nil {
		return fmt.Errorf("connect postgres: %w", err)
	}
	defer conn.Close(ctx)

	// Perform hybrid search
	s := store.NewStore(conn)
	semCandidates, err := s.SemanticCandidates(ctx, vec, cfg.Embedding.Model, limit)
	if err != nil {
		return err
	}
	ftsCandidates, err := s.KeywordCandidates(ctx, query, limit)
	if err != nil {
		return err
	}

	// RRF on semantic and keyword candidates
	results := search.Fuse([][]store.Candidate{semCandidates, ftsCandidates}, limit)

	for _, r := range results {
		title := r.Title
		if title == "" {
			title = "(untitled)"
		}
		fmt.Printf("%s — %s\n\n", title, r.URI)
		fmt.Println(r.Text)
		fmt.Println("================================")
	}

	return nil
}
