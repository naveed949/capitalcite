suite=golden sha=7388ac3eea430b6ce44e318c79aa9fd87ed71e99 small=true items=77 k=5 embedder=fake:deterministic sparse=fake:bm25:k1=1.2:b=0.75:avgdl=256.0 fastembed=0.8.1 qdrant-client=1.19.1

| config_hash | chunker | embedder | mode | n | Recall@5 | MRR@5 | nDCG@5 | Recall@10 | MRR@10 | nDCG@10 | misses@5 |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 625ed34ac394 | section | fake:deterministic | dense | 77 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 77 (see JSON) |
| 625ed34ac394 | section | fake:deterministic | bm25 | 77 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 77 (see JSON) |
| 625ed34ac394 | section | fake:deterministic | hybrid | 77 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 77 (see JSON) |
| 72ee627028aa | fixed | fake:deterministic | dense | 77 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 77 (see JSON) |
| 72ee627028aa | fixed | fake:deterministic | bm25 | 77 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 77 (see JSON) |
| 72ee627028aa | fixed | fake:deterministic | hybrid | 77 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 77 (see JSON) |

Misses@5
- section/dense: cc-h-001, cc-h-002, cc-h-003, cc-h-004, cc-h-005, cc-h-006, cc-h-007, cc-h-008, ... (+69)
- section/bm25: cc-h-001, cc-h-002, cc-h-003, cc-h-004, cc-h-005, cc-h-006, cc-h-007, cc-h-008, ... (+69)
- section/hybrid: cc-h-001, cc-h-002, cc-h-003, cc-h-004, cc-h-005, cc-h-006, cc-h-007, cc-h-008, ... (+69)
- fixed/dense: cc-h-001, cc-h-002, cc-h-003, cc-h-004, cc-h-005, cc-h-006, cc-h-007, cc-h-008, ... (+69)
- fixed/bm25: cc-h-001, cc-h-002, cc-h-003, cc-h-004, cc-h-005, cc-h-006, cc-h-007, cc-h-008, ... (+69)
- fixed/hybrid: cc-h-001, cc-h-002, cc-h-003, cc-h-004, cc-h-005, cc-h-006, cc-h-007, cc-h-008, ... (+69)

A hit is relevant when chunk.doc_id matches and the half-open spans overlap.
Recall@k is the fraction of items with any relevant hit in the top k.
MRR@k is the mean of 1/rank of the first relevant hit (0 if past k).
nDCG@k credits each gold span once, at its first covering hit, and is clamped at 1.
Noise floor: n=77, one item is 0.013 recall.
RAGAS context precision/recall and hybrid+rerank are UNVERIFIED.
Probe: section/hybrid cc-h-013 absent; fixed/hybrid cc-h-013 absent.

Agent vs RAG retrieval on comparison and multi-hop items (n=15, section/hybrid, fake/heuristic planner).
Plain RAG Recall@5=0.000 Recall@10=0.000.
Agent Recall@5=0.000 Recall@10=0.000.
Answer quality stays UNVERIFIED.
