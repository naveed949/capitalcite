BM25 diagnostic
collection=filings-625ed34ac3946fd5b47a780fa504fa41784e7621b8873514668abd2c5c95e1cd points probe_k=2000
chunker=section embedder=fake:deterministic
sparse_id=fake:bm25:k1=1.2:b=0.75:avgdl=256.0
idf_modifier=IDF
embed_query builds the dense vector (BGE may prefix that vector) and pairs FastEmbed query_embed() (or FakeEmbedder bm25_query) of the raw query as the sparse vector: unique terms, weight 1. embed_documents pairs embed() BM25 TF: tf*(k1+1)/(tf+k1*(1-b+b*dl/avgdl)) with k1=1.2 b=0.75 avgdl=256. Stopwords are dropped. Production stems. BM25 search sends only the sparse vector.
CET1 survives as cet1 (and stems to cet1).
rare_token_examples=['cet1', 'wells', 'fargo', 'slr', '2025']

## cc-h-013
question=What was JPMorgan's Standardized CET1 capital ratio at year-end 2024?
raw_tokens=['what', 'was', 'jpmorgan', 's', 'standardized', 'cet1', 'capital', 'ratio', 'at', 'year', 'end', '2024']
query_embed_terms=['jpmorgan', 'standardized', 'cet1', 'capital', 'ratio', 'year', 'end', '2024']
sparse_dims=8 query_values_all_ones=true
gold 0000019617-25-000270 [487805,487867) quote_tokens=['the', 'standardized', 'and', 'advanced', 'cet1', 'ratios', 'were', '15', '7', 'and', '15', '8']
gold_quote_has_query_terms=['standardized', 'cet1']
gold_quote_missing_query_terms=['jpmorgan', 'capital', 'ratio', 'year', 'end', '2024']
gold_covering_chunks=none
gold_chunk_bm25_rank=>2000
bm25_top5:
  1. score=9.6017 ticker=JPM doc_id=0000019617-25-000270 [252,462) gold=False "Item 7. Management's Discussion and Analysis of Financial Condition and Results of Operations Net income increased year over year. | Metric | 2024 | 2025 | | --"
  2. score=4.1609 ticker=JPM doc_id=0000019617-25-000271 [6340,7904) gold=False '| Metric | 2024 | 2025 | Notes | | --- | --- | --- | --- | | Capital metric 64 | 15.10% | 16.00% | year-over-year change and regulatory buffer notes | | Capital'
  3. score=4.0965 ticker=JPM doc_id=0000019617-25-000271 [2476,4408) gold=False '| Metric | 2024 | 2025 | Notes | | --- | --- | --- | --- | | Capital metric 22 | 15.10% | 16.00% | year-over-year change and regulatory buffer notes | | Capital'
  4. score=4.0965 ticker=JPM doc_id=0000019617-25-000271 [4408,6340) gold=False '| Metric | 2024 | 2025 | Notes | | --- | --- | --- | --- | | Capital metric 43 | 15.10% | 16.00% | year-over-year change and regulatory buffer notes | | Capital'
  5. score=4.0965 ticker=JPM doc_id=0000019617-25-000271 [485,2476) gold=False '| Metric | 2024 | 2025 | Notes | | --- | --- | --- | --- | | Capital metric 01 | 15.10% | 16.00% | year-over-year change and regulatory buffer notes | | Capital'

## cc-h-063
question=What CET1 ratio did Wells Fargo report under the Standardized Approach at year-end 2025?
raw_tokens=['what', 'cet1', 'ratio', 'did', 'wells', 'fargo', 'report', 'under', 'the', 'standardized', 'approach', 'at', 'year', 'end', '2025']
query_embed_terms=['cet1', 'ratio', 'wells', 'fargo', 'report', 'standardized', 'approach', 'year', 'end', '2025']
sparse_dims=10 query_values_all_ones=true
gold 0000072971-26-000133 [228593,228647) quote_tokens=['cet1', 'ratio', 'was', '10', '61', 'under', 'the', 'standardized', 'approach']
gold_quote_has_query_terms=['cet1', 'ratio', 'standardized', 'approach']
gold_quote_missing_query_terms=['wells', 'fargo', 'report', 'year', 'end', '2025']
gold_covering_chunks=none
gold_chunk_bm25_rank=>2000
bm25_top5:
  1. score=9.6017 ticker=JPM doc_id=0000019617-25-000270 [252,462) gold=False "Item 7. Management's Discussion and Analysis of Financial Condition and Results of Operations Net income increased year over year. | Metric | 2024 | 2025 | | --"
  2. score=2.3825 ticker=JPM doc_id=0000019617-25-000271 [6340,7904) gold=False '| Metric | 2024 | 2025 | Notes | | --- | --- | --- | --- | | Capital metric 64 | 15.10% | 16.00% | year-over-year change and regulatory buffer notes | | Capital'
  3. score=2.3123 ticker=JPM doc_id=0000019617-25-000271 [2476,4408) gold=False '| Metric | 2024 | 2025 | Notes | | --- | --- | --- | --- | | Capital metric 22 | 15.10% | 16.00% | year-over-year change and regulatory buffer notes | | Capital'
  4. score=2.3123 ticker=JPM doc_id=0000019617-25-000271 [4408,6340) gold=False '| Metric | 2024 | 2025 | Notes | | --- | --- | --- | --- | | Capital metric 43 | 15.10% | 16.00% | year-over-year change and regulatory buffer notes | | Capital'
  5. score=2.3123 ticker=JPM doc_id=0000019617-25-000271 [485,2476) gold=False '| Metric | 2024 | 2025 | Notes | | --- | --- | --- | --- | | Capital metric 01 | 15.10% | 16.00% | year-over-year change and regulatory buffer notes | | Capital'

## cc-h-001
question=What was JPMorgan's Standardized CET1 capital ratio at year-end 2023?
raw_tokens=['what', 'was', 'jpmorgan', 's', 'standardized', 'cet1', 'capital', 'ratio', 'at', 'year', 'end', '2023']
query_embed_terms=['jpmorgan', 'standardized', 'cet1', 'capital', 'ratio', 'year', 'end', '2023']
sparse_dims=8 query_values_all_ones=true
gold 0000019617-24-000225 [488083,488140) quote_tokens=['the', 'standardized', 'and', 'advanced', 'cet1', 'ratios', 'were', 'both', '15', '0']
gold_quote_has_query_terms=['standardized', 'cet1']
gold_quote_missing_query_terms=['jpmorgan', 'capital', 'ratio', 'year', 'end', '2023']
gold_covering_chunks=none
gold_chunk_bm25_rank=>2000
bm25_top5:
  1. score=8.2207 ticker=JPM doc_id=0000019617-25-000270 [252,462) gold=False "Item 7. Management's Discussion and Analysis of Financial Condition and Results of Operations Net income increased year over year. | Metric | 2024 | 2025 | | --"
  2. score=3.2643 ticker=JPM doc_id=0000019617-25-000271 [2476,4408) gold=False '| Metric | 2024 | 2025 | Notes | | --- | --- | --- | --- | | Capital metric 22 | 15.10% | 16.00% | year-over-year change and regulatory buffer notes | | Capital'
  3. score=3.2643 ticker=JPM doc_id=0000019617-25-000271 [485,2476) gold=False '| Metric | 2024 | 2025 | Notes | | --- | --- | --- | --- | | Capital metric 01 | 15.10% | 16.00% | year-over-year change and regulatory buffer notes | | Capital'
  4. score=3.2643 ticker=JPM doc_id=0000019617-25-000271 [4408,6340) gold=False '| Metric | 2024 | 2025 | Notes | | --- | --- | --- | --- | | Capital metric 43 | 15.10% | 16.00% | year-over-year change and regulatory buffer notes | | Capital'
  5. score=3.2558 ticker=JPM doc_id=0000019617-25-000271 [6340,7904) gold=False '| Metric | 2024 | 2025 | Notes | | --- | --- | --- | --- | | Capital metric 64 | 15.10% | 16.00% | year-over-year change and regulatory buffer notes | | Capital'


BM25 diagnostic
collection=filings-72ee627028aa417212488f56af2fbfc74798a2ec7986c1cbbf86ba9110e25c25 points probe_k=2000
chunker=fixed embedder=fake:deterministic
sparse_id=fake:bm25:k1=1.2:b=0.75:avgdl=256.0
idf_modifier=IDF
embed_query builds the dense vector (BGE may prefix that vector) and pairs FastEmbed query_embed() (or FakeEmbedder bm25_query) of the raw query as the sparse vector: unique terms, weight 1. embed_documents pairs embed() BM25 TF: tf*(k1+1)/(tf+k1*(1-b+b*dl/avgdl)) with k1=1.2 b=0.75 avgdl=256. Stopwords are dropped. Production stems. BM25 search sends only the sparse vector.
CET1 survives as cet1 (and stems to cet1).
rare_token_examples=['cet1', 'wells', 'fargo', 'slr', '2025']

## cc-h-013
question=What was JPMorgan's Standardized CET1 capital ratio at year-end 2024?
raw_tokens=['what', 'was', 'jpmorgan', 's', 'standardized', 'cet1', 'capital', 'ratio', 'at', 'year', 'end', '2024']
query_embed_terms=['jpmorgan', 'standardized', 'cet1', 'capital', 'ratio', 'year', 'end', '2024']
sparse_dims=8 query_values_all_ones=true
gold 0000019617-25-000270 [487805,487867) quote_tokens=['the', 'standardized', 'and', 'advanced', 'cet1', 'ratios', 'were', '15', '7', 'and', '15', '8']
gold_quote_has_query_terms=['standardized', 'cet1']
gold_quote_missing_query_terms=['jpmorgan', 'capital', 'ratio', 'year', 'end', '2024']
gold_covering_chunks=none
gold_chunk_bm25_rank=>2000
bm25_top5:
  1. score=7.2947 ticker=JPM doc_id=0000019617-25-000270 [385,461) gold=False '| Metric | 2024 | 2025 | | --- | --- | --- | | CET1 ratio | 15.1% | 16.0% |'
  2. score=2.7832 ticker=JPM doc_id=0000019617-25-000271 [6340,7904) gold=False '| Metric | 2024 | 2025 | Notes | | --- | --- | --- | --- | | Capital metric 64 | 15.10% | 16.00% | year-over-year change and regulatory buffer notes | | Capital'
  3. score=2.7383 ticker=JPM doc_id=0000019617-25-000271 [485,2476) gold=False '| Metric | 2024 | 2025 | Notes | | --- | --- | --- | --- | | Capital metric 01 | 15.10% | 16.00% | year-over-year change and regulatory buffer notes | | Capital'
  4. score=2.7383 ticker=JPM doc_id=0000019617-25-000271 [2476,4408) gold=False '| Metric | 2024 | 2025 | Notes | | --- | --- | --- | --- | | Capital metric 22 | 15.10% | 16.00% | year-over-year change and regulatory buffer notes | | Capital'
  5. score=2.7383 ticker=JPM doc_id=0000019617-25-000271 [4408,6340) gold=False '| Metric | 2024 | 2025 | Notes | | --- | --- | --- | --- | | Capital metric 43 | 15.10% | 16.00% | year-over-year change and regulatory buffer notes | | Capital'

## cc-h-063
question=What CET1 ratio did Wells Fargo report under the Standardized Approach at year-end 2025?
raw_tokens=['what', 'cet1', 'ratio', 'did', 'wells', 'fargo', 'report', 'under', 'the', 'standardized', 'approach', 'at', 'year', 'end', '2025']
query_embed_terms=['cet1', 'ratio', 'wells', 'fargo', 'report', 'standardized', 'approach', 'year', 'end', '2025']
sparse_dims=10 query_values_all_ones=true
gold 0000072971-26-000133 [228593,228647) quote_tokens=['cet1', 'ratio', 'was', '10', '61', 'under', 'the', 'standardized', 'approach']
gold_quote_has_query_terms=['cet1', 'ratio', 'standardized', 'approach']
gold_quote_missing_query_terms=['wells', 'fargo', 'report', 'year', 'end', '2025']
gold_covering_chunks=none
gold_chunk_bm25_rank=>2000
bm25_top5:
  1. score=7.2947 ticker=JPM doc_id=0000019617-25-000270 [385,461) gold=False '| Metric | 2024 | 2025 | | --- | --- | --- | | CET1 ratio | 15.1% | 16.0% |'
  2. score=1.5473 ticker=JPM doc_id=0000019617-25-000271 [6340,7904) gold=False '| Metric | 2024 | 2025 | Notes | | --- | --- | --- | --- | | Capital metric 64 | 15.10% | 16.00% | year-over-year change and regulatory buffer notes | | Capital'
  3. score=1.4983 ticker=JPM doc_id=0000019617-25-000271 [485,2476) gold=False '| Metric | 2024 | 2025 | Notes | | --- | --- | --- | --- | | Capital metric 01 | 15.10% | 16.00% | year-over-year change and regulatory buffer notes | | Capital'
  4. score=1.4983 ticker=JPM doc_id=0000019617-25-000271 [2476,4408) gold=False '| Metric | 2024 | 2025 | Notes | | --- | --- | --- | --- | | Capital metric 22 | 15.10% | 16.00% | year-over-year change and regulatory buffer notes | | Capital'
  5. score=1.4983 ticker=JPM doc_id=0000019617-25-000271 [4408,6340) gold=False '| Metric | 2024 | 2025 | Notes | | --- | --- | --- | --- | | Capital metric 43 | 15.10% | 16.00% | year-over-year change and regulatory buffer notes | | Capital'

## cc-h-001
question=What was JPMorgan's Standardized CET1 capital ratio at year-end 2023?
raw_tokens=['what', 'was', 'jpmorgan', 's', 'standardized', 'cet1', 'capital', 'ratio', 'at', 'year', 'end', '2023']
query_embed_terms=['jpmorgan', 'standardized', 'cet1', 'capital', 'ratio', 'year', 'end', '2023']
sparse_dims=8 query_values_all_ones=true
gold 0000019617-24-000225 [488083,488140) quote_tokens=['the', 'standardized', 'and', 'advanced', 'cet1', 'ratios', 'were', 'both', '15', '0']
gold_quote_has_query_terms=['standardized', 'cet1']
gold_quote_missing_query_terms=['jpmorgan', 'capital', 'ratio', 'year', 'end', '2023']
gold_covering_chunks=none
gold_chunk_bm25_rank=>2000
bm25_top5:
  1. score=6.3017 ticker=JPM doc_id=0000019617-25-000270 [385,461) gold=False '| Metric | 2024 | 2025 | | --- | --- | --- | | CET1 ratio | 15.1% | 16.0% |'
  2. score=2.1598 ticker=JPM doc_id=0000019617-25-000271 [485,2476) gold=False '| Metric | 2024 | 2025 | Notes | | --- | --- | --- | --- | | Capital metric 01 | 15.10% | 16.00% | year-over-year change and regulatory buffer notes | | Capital'
  3. score=2.1598 ticker=JPM doc_id=0000019617-25-000271 [4408,6340) gold=False '| Metric | 2024 | 2025 | Notes | | --- | --- | --- | --- | | Capital metric 43 | 15.10% | 16.00% | year-over-year change and regulatory buffer notes | | Capital'
  4. score=2.1598 ticker=JPM doc_id=0000019617-25-000271 [2476,4408) gold=False '| Metric | 2024 | 2025 | Notes | | --- | --- | --- | --- | | Capital metric 22 | 15.10% | 16.00% | year-over-year change and regulatory buffer notes | | Capital'
  5. score=2.1542 ticker=JPM doc_id=0000019617-25-000271 [6340,7904) gold=False '| Metric | 2024 | 2025 | Notes | | --- | --- | --- | --- | | Capital metric 64 | 15.10% | 16.00% | year-over-year change and regulatory buffer notes | | Capital'

