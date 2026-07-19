package store

import (
	"context"
	"fmt"
	"strconv"
	"strings"

	"github.com/jackc/pgx/v5"
)

type Candidate struct {
	ChunkID    string
	DocumentID string
	Position   int
	Text       string
	Title      string
	URI        string
}

/*
Store object:
- Can fetch SemanticCandidates
- Can fetch FTSCandidates
- Contains a connection to the PQDB
*/

type Store struct {
	conn *pgx.Conn
}

func NewStore(conn *pgx.Conn) *Store {
	return &Store{conn: conn}
}

/*
Fetch top-k chunks by semantic similarity from active documents
- Measure distance between query embedding and chunk embeddings
- <=> means cosine distance
- Results are approximate, because we created a HNSW index
*/
const semanticSQL = `
SELECT c.id, c.document_id, c.position, c.text, d.title, d.uri 
FROM embeddings e 
JOIN chunks c ON c.id = e.chunk_id
JOIN documents d ON d.id = c.document_id
WHERE e.model = $2 AND d.status = 'active'
ORDER BY e.vector <=> $1::vector
LIMIT $3
`

func (s *Store) SemanticCandidates(ctx context.Context, queryVec []float32, model string, k int) ([]Candidate, error) {
	rows, err := s.conn.Query(ctx, semanticSQL, vectorLiteral(queryVec), model, k)
	if err != nil {
		return nil, fmt.Errorf("semantic PQ query: %w", err)
	}

	candidates, err := processRows(rows, k)
	if err != nil {
		return nil, err
	}

	return candidates, nil
}

/*
Fetch top-k chunks through pg FTS from active documents
- websearch_to_tsquery: Convert query to PGs internal tsvector format (stemming, stop word removal, and so on)
- Use 'english' as it was defined in the chunks table definition
- @@ performs the boolean matching on the query tsvector and the chunks' tsvector -> Removes chunks that don't match
- ts_rank_cd computes the relevance scores for the chunks and ranks them
  - finds covers: smallest contiguous span of positions that contains query terms
  - scores dense covers highly. I.e., the closer the query terms are together, the higher the score
*/
const keywordSQL = `
SELECT c.id, c.document_id, c.position, c.text, d.title, d.uri
FROM chunks c
JOIN documents d ON d.id = c.document_id,
	websearch_to_tsquery('english', $1) q 
WHERE c.text_search @@ q AND d.status = 'active'
ORDER BY ts_rank_cd(c.text_search, q) DESC
LIMIT $2
`

func (s *Store) KeywordCandidates(ctx context.Context, query string, k int) ([]Candidate, error) {
	rows, err := s.conn.Query(ctx, keywordSQL, query, k)
	if err != nil {
		return nil, fmt.Errorf("keyword PQ query: %w", err)
	}

	candidates, err := processRows(rows, k)
	if err != nil {
		return nil, err
	}

	return candidates, nil
}

func processRows(rows pgx.Rows, k int) ([]Candidate, error) {
	defer rows.Close()

	candidates := make([]Candidate, 0, k)
	for rows.Next() {
		var candidate Candidate
		var title *string // Title is nullable

		err := rows.Scan(
			&candidate.ChunkID,
			&candidate.DocumentID,
			&candidate.Position,
			&candidate.Text,
			&title,
			&candidate.URI,
		)

		if err != nil {
			return nil, fmt.Errorf("scanning candidate: %w", err)
		}

		if title != nil {
			candidate.Title = *title
		}

		candidates = append(candidates, candidate)
	}

	if err := rows.Err(); err != nil {
		return nil, err
	}

	return candidates, nil
}

// Convert embedding to string representation
func vectorLiteral(v []float32) string {
	parts := make([]string, len(v))
	for i, f := range v {
		parts[i] = strconv.FormatFloat(float64(f), 'f', -1, 32)
	}
	return "[" + strings.Join(parts, ",") + "]"
}
