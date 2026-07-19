package search

import (
	"sort"

	"github.com/LaurentLeFebveDeVivy/vivy-atlas/server/internal/store"
)

/*
- TLDR: Reward candidates:
  - with high ranks
  - appearing in multiple sources

- Aggregate RRF scores by ChunkID
- Sort by decreasing scores and keep at most limit
*/
func Fuse(legs [][]store.Candidate, limit int) []store.Candidate {

	const alpha = 60
	scores := map[string]float64{}
	byID := map[string]store.Candidate{}

	for _, leg := range legs {
		for rank, c := range leg {
			scores[c.ChunkID] += 1.0 / float64(alpha+rank+1)
			byID[c.ChunkID] = c
		}
	}

	fused := make([]store.Candidate, 0, len(scores))
	for id := range scores {
		fused = append(fused, byID[id])
	}

	sort.Slice(fused, func(i, j int) bool {
		return scores[fused[i].ChunkID] > scores[fused[j].ChunkID]
	})

	if len(fused) > limit {
		fused = fused[:limit]
	}

	return fused
}
