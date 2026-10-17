BM25 diagnostic
collection=filings-3121e257bc0cdcaf7c7ed473fdf184458e35778aa9bcfc10ad1e2abd72e11880 points probe_k=2000
chunker=section embedder=fastembed:BAAI/bge-small-en-v1.5
sparse_id=fastembed:Qdrant/bm25:k1=1.2:b=0.75:avgdl=256.0
idf_modifier=IDF
embed_query builds the dense vector (BGE may prefix that vector) and pairs FastEmbed query_embed() (or FakeEmbedder bm25_query) of the raw query as the sparse vector: unique terms, weight 1. embed_documents pairs embed() BM25 TF: tf*(k1+1)/(tf+k1*(1-b+b*dl/avgdl)) with k1=1.2 b=0.75 avgdl=256. Stopwords are dropped. Production stems. BM25 search sends only the sparse vector.
CET1 survives as cet1 (and stems to cet1).
rare_token_examples=['cet1', 'wells', 'fargo', 'slr', '2025']

## cc-h-013
question=What was JPMorgan's Standardized CET1 capital ratio at year-end 2024?
raw_tokens=['what', 'was', 'jpmorgan', 's', 'standardized', 'cet1', 'capital', 'ratio', 'at', 'year', 'end', '2024']
query_embed_terms=['jpmorgan', 'standard', 'cet1', 'capit', 'ratio', 'year', 'end', '2024']
sparse_dims=8 query_values_all_ones=true
gold 0000019617-25-000270 [487805,487867) quote_tokens=['the', 'standardized', 'and', 'advanced', 'cet1', 'ratios', 'were', '15', '7', 'and', '15', '8']
gold_quote_has_query_terms=['cet1']
gold_quote_missing_query_terms=['jpmorgan', 'standard', 'capit', 'ratio', 'year', 'end', '2024']
gold_chunk [485869,487907) chars=2038 tokens=339 section='Item 7'
gold_chunk_has_query_terms=['cet1', 'ratio', 'year', '2024']
gold_chunk_missing_query_terms=['jpmorgan', 'standard', 'capit', 'end']
gold_chunk_has_rare_tokens=['cet1', 'slr']
gold_chunk_snippet='was $10.7 billion, reflecting $8.6 billion of net charge-offs and a net addition to the allowance for credit losses of $2.0 billion. Net charge-offs increased b'
gold_chunk [487651,489193) chars=1542 tokens=235 section='Item 7'
gold_chunk_has_query_terms=['jpmorgan', 'cet1', 'year', 'end', '2024']
gold_chunk_missing_query_terms=['standard', 'capit', 'ratio']
gold_chunk_has_rare_tokens=['cet1', 'slr']
gold_chunk_snippet='Refer to Liquidity Risk Management on pages 108–115 for additional information. Selected capital and other metrics • CET1 capital was $275.5 billion, and the St'
gold_chunk_bm25_rank=12
bm25_top5:
  1. score=29.8377 ticker=JPM doc_id=0001628280-26-008131 [1550629,1552278) gold=False 'Note: The table above is as defined by the regulations issued by the Federal Reserve, OCC and FDIC and to which the Firm and JPMorgan Chase Bank, N.A. are subje'
  2. score=29.2605 ticker=C doc_id=0000831001-25-000067 [521284,522998) gold=False '37 Common Equity Tier 1 Capital Ratio Citi’s Common Equity Tier 1 (CET1) Capital ratio under the Basel III Standardized Approach was 13.6% as of December 31, 20'
  3. score=27.5230 ticker=JPM doc_id=0000019617-25-000270 [1576405,1578096) gold=False 'Note: The table above is as defined by the regulations issued by the Federal Reserve, OCC and FDIC and to which the Firm and JPMorgan Chase Bank, N.A. are subje'
  4. score=26.7725 ticker=JPM doc_id=0001628280-26-008131 [649201,650792) gold=False '(a) As of January 1, 2025, the benefit from the CECL capital transition provision had been fully phased out. For the year ended December 31, 2024, CET1 capital '
  5. score=26.7204 ticker=JPM doc_id=0000019617-24-000225 [1613318,1615065) gold=False 'Note: The table above is as defined by the regulations issued by the Federal Reserve, OCC and FDIC and to which the Firm and JPMorgan Chase Bank, N.A. are subje'

## cc-h-063
question=What CET1 ratio did Wells Fargo report under the Standardized Approach at year-end 2025?
raw_tokens=['what', 'cet1', 'ratio', 'did', 'wells', 'fargo', 'report', 'under', 'the', 'standardized', 'approach', 'at', 'year', 'end', '2025']
query_embed_terms=['cet1', 'ratio', 'well', 'fargo', 'report', 'standard', 'approach', 'year', 'end', '2025']
sparse_dims=10 query_values_all_ones=true
gold 0000072971-26-000133 [228593,228647) quote_tokens=['cet1', 'ratio', 'was', '10', '61', 'under', 'the', 'standardized', 'approach']
gold_quote_has_query_terms=['cet1', 'ratio', 'approach']
gold_quote_missing_query_terms=['well', 'fargo', 'report', 'standard', 'year', 'end', '2025']
gold_chunk [227583,229470) chars=1887 tokens=282 section='Item 7'
gold_chunk_has_query_terms=['cet1', 'ratio', 'report', 'approach', '2025']
gold_chunk_missing_query_terms=['well', 'fargo', 'standard', 'year', 'end']
gold_chunk_has_rare_tokens=['cet1', '2025']
gold_chunk_snippet='Financial Performance In 2025, we generated $21.3 billion of net income and diluted earnings per share (EPS) of $6.26, compared with $19.7 billion of net income'
gold_chunk_bm25_rank=113
bm25_top5:
  1. score=31.5475 ticker=C doc_id=0000831001-25-000067 [521284,522998) gold=False '37 Common Equity Tier 1 Capital Ratio Citi’s Common Equity Tier 1 (CET1) Capital ratio under the Basel III Standardized Approach was 13.6% as of December 31, 20'
  2. score=29.9343 ticker=JPM doc_id=0001628280-26-008131 [649201,650792) gold=False '(a) As of January 1, 2025, the benefit from the CECL capital transition provision had been fully phased out. For the year ended December 31, 2024, CET1 capital '
  3. score=28.9838 ticker=BAC doc_id=0000070858-26-000157 [1331722,1333479) gold=False '(1) As of January 1, 2025, CECL transition provision’s impact was fully phased-in. Capital ratios as of December 31, 2024 were calculated using the regulatory c'
  4. score=28.9268 ticker=WFC doc_id=0000072971-26-000133 [235556,237502) gold=False '| | | | | | | | | | | | | | | | | | | | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | | | Year end'
  5. score=28.9113 ticker=C doc_id=0000831001-24-000033 [505644,507386) gold=False '35 Common Equity Tier 1 Capital Ratio Citi’s Common Equity Tier 1 (CET1) Capital ratio under the Basel III Standardized Approach was 13.4% as of December 31, 20'

## cc-h-001
question=What was JPMorgan's Standardized CET1 capital ratio at year-end 2023?
raw_tokens=['what', 'was', 'jpmorgan', 's', 'standardized', 'cet1', 'capital', 'ratio', 'at', 'year', 'end', '2023']
query_embed_terms=['jpmorgan', 'standard', 'cet1', 'capit', 'ratio', 'year', 'end', '2023']
sparse_dims=8 query_values_all_ones=true
gold 0000019617-24-000225 [488083,488140) quote_tokens=['the', 'standardized', 'and', 'advanced', 'cet1', 'ratios', 'were', 'both', '15', '0']
gold_quote_has_query_terms=['cet1']
gold_quote_missing_query_terms=['jpmorgan', 'standard', 'capit', 'ratio', 'year', 'end', '2023']
gold_chunk [487188,489201) chars=2013 tokens=308 section='Item 7'
gold_chunk_has_query_terms=['jpmorgan', 'cet1', 'end', '2023']
gold_chunk_missing_query_terms=['standard', 'capit', 'ratio', 'year']
gold_chunk_has_rare_tokens=['cet1', 'slr']
gold_chunk_snippet='| | | | | | | | | | | --- | --- | --- | --- | --- | --- | --- | --- | --- | | JPMorgan Chase & Co./2023 Form 10-K | | 49 | | | | | | | • Firmwide average loans '
gold_chunk_bm25_rank=53
bm25_top5:
  1. score=28.9463 ticker=C doc_id=0000831001-25-000067 [521284,522998) gold=False '37 Common Equity Tier 1 Capital Ratio Citi’s Common Equity Tier 1 (CET1) Capital ratio under the Basel III Standardized Approach was 13.6% as of December 31, 20'
  2. score=28.2802 ticker=C doc_id=0000831001-24-000033 [505644,507386) gold=False '35 Common Equity Tier 1 Capital Ratio Citi’s Common Equity Tier 1 (CET1) Capital ratio under the Basel III Standardized Approach was 13.4% as of December 31, 20'
  3. score=28.2286 ticker=JPM doc_id=0001628280-26-008131 [1550629,1552278) gold=False 'Note: The table above is as defined by the regulations issued by the Federal Reserve, OCC and FDIC and to which the Firm and JPMorgan Chase Bank, N.A. are subje'
  4. score=27.7580 ticker=JPM doc_id=0000019617-24-000225 [1613318,1615065) gold=False 'Note: The table above is as defined by the regulations issued by the Federal Reserve, OCC and FDIC and to which the Firm and JPMorgan Chase Bank, N.A. are subje'
  5. score=27.7168 ticker=JPM doc_id=0000019617-25-000270 [1576405,1578096) gold=False 'Note: The table above is as defined by the regulations issued by the Federal Reserve, OCC and FDIC and to which the Firm and JPMorgan Chase Bank, N.A. are subje'


BM25 diagnostic
collection=filings-b95a2e0587bce93f3541408204cff48f899cb157fe91ea322a2c939055c33bc7 points probe_k=2000
chunker=fixed embedder=fastembed:BAAI/bge-small-en-v1.5
sparse_id=fastembed:Qdrant/bm25:k1=1.2:b=0.75:avgdl=256.0
idf_modifier=IDF
embed_query builds the dense vector (BGE may prefix that vector) and pairs FastEmbed query_embed() (or FakeEmbedder bm25_query) of the raw query as the sparse vector: unique terms, weight 1. embed_documents pairs embed() BM25 TF: tf*(k1+1)/(tf+k1*(1-b+b*dl/avgdl)) with k1=1.2 b=0.75 avgdl=256. Stopwords are dropped. Production stems. BM25 search sends only the sparse vector.
CET1 survives as cet1 (and stems to cet1).
rare_token_examples=['cet1', 'wells', 'fargo', 'slr', '2025']

## cc-h-013
question=What was JPMorgan's Standardized CET1 capital ratio at year-end 2024?
raw_tokens=['what', 'was', 'jpmorgan', 's', 'standardized', 'cet1', 'capital', 'ratio', 'at', 'year', 'end', '2024']
query_embed_terms=['jpmorgan', 'standard', 'cet1', 'capit', 'ratio', 'year', 'end', '2024']
sparse_dims=8 query_values_all_ones=true
gold 0000019617-25-000270 [487805,487867) quote_tokens=['the', 'standardized', 'and', 'advanced', 'cet1', 'ratios', 'were', '15', '7', 'and', '15', '8']
gold_quote_has_query_terms=['cet1']
gold_quote_missing_query_terms=['jpmorgan', 'standard', 'capit', 'ratio', 'year', 'end', '2024']
gold_chunk [487080,488906) chars=1826 tokens=294 section='Item 7'
gold_chunk_has_query_terms=['cet1', 'end', '2024']
gold_chunk_missing_query_terms=['jpmorgan', 'standard', 'capit', 'ratio', 'year']
gold_chunk_has_rare_tokens=['cet1', 'slr']
gold_chunk_snippet='6–136 and pages 120–125, respectively, for additional information. • Firmwide average loans of $1.3 trillion were up 6%, driven by higher loans across the lines'
gold_chunk_bm25_rank=96
bm25_top5:
  1. score=33.1610 ticker=JPM doc_id=0001628280-26-008131 [1550629,1552278) gold=False 'Note: The table above is as defined by the regulations issued by the Federal Reserve, OCC and FDIC and to which the Firm and JPMorgan Chase Bank, N.A. are subje'
  2. score=30.9257 ticker=C doc_id=0000831001-25-000067 [521820,523000) gold=False 'III Advanced Approaches was 12.1% as of December 31, 2024, 12.2% as of September 30, 2024, and 12.1% as of December 31, 2023, relative to a required regulatory '
  3. score=30.5113 ticker=JPM doc_id=0000019617-25-000270 [1576405,1578096) gold=False 'Note: The table above is as defined by the regulations issued by the Federal Reserve, OCC and FDIC and to which the Firm and JPMorgan Chase Bank, N.A. are subje'
  4. score=29.8254 ticker=C doc_id=0000831001-26-000011 [504162,506210) gold=False '(1) Commencing January 1, 2025, the capital effects resulting from adoption of the current expected credit losses (CECL) methodology have been fully reflected i'
  5. score=29.5448 ticker=JPM doc_id=0000019617-24-000225 [1613318,1615065) gold=False 'Note: The table above is as defined by the regulations issued by the Federal Reserve, OCC and FDIC and to which the Firm and JPMorgan Chase Bank, N.A. are subje'

## cc-h-063
question=What CET1 ratio did Wells Fargo report under the Standardized Approach at year-end 2025?
raw_tokens=['what', 'cet1', 'ratio', 'did', 'wells', 'fargo', 'report', 'under', 'the', 'standardized', 'approach', 'at', 'year', 'end', '2025']
query_embed_terms=['cet1', 'ratio', 'well', 'fargo', 'report', 'standard', 'approach', 'year', 'end', '2025']
sparse_dims=10 query_values_all_ones=true
gold 0000072971-26-000133 [228593,228647) quote_tokens=['cet1', 'ratio', 'was', '10', '61', 'under', 'the', 'standardized', 'approach']
gold_quote_has_query_terms=['cet1', 'ratio', 'approach']
gold_quote_missing_query_terms=['well', 'fargo', 'report', 'standard', 'year', 'end', '2025']
gold_chunk [226962,229010) chars=2048 tokens=314 section='Item 7'
gold_chunk_has_query_terms=['cet1', 'ratio', 'well', 'fargo', 'approach', '2025']
gold_chunk_missing_query_terms=['report', 'standard', 'year', 'end']
gold_chunk_has_rare_tokens=['cet1', 'wells', 'fargo', '2025']
gold_chunk_snippet='Wells Fargo & Company is a leading financial services company that has approximately $2.1 trillion in assets. We provide a diversified set of banking, investmen'
gold_chunk_bm25_rank=17
bm25_top5:
  1. score=35.6103 ticker=C doc_id=0000831001-26-000011 [504162,506210) gold=False '(1) Commencing January 1, 2025, the capital effects resulting from adoption of the current expected credit losses (CECL) methodology have been fully reflected i'
  2. score=33.7692 ticker=C doc_id=0000831001-25-000067 [521820,523000) gold=False 'III Advanced Approaches was 12.1% as of December 31, 2024, 12.2% as of September 30, 2024, and 12.1% as of December 31, 2023, relative to a required regulatory '
  3. score=32.6569 ticker=JPM doc_id=0001628280-26-008131 [649201,650792) gold=False '(a) As of January 1, 2025, the benefit from the CECL capital transition provision had been fully phased out. For the year ended December 31, 2024, CET1 capital '
  4. score=31.7815 ticker=WFC doc_id=0000072971-26-000133 [235556,237502) gold=False '| | | | | | | | | | | | | | | | | | | | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | | | Year end'
  5. score=31.4044 ticker=C doc_id=0000831001-24-000033 [506434,507793) gold=False 'res to a CET1 Capital ratio of 12.2% as of December 31, 2022, relative to a required regulatory CET1 Capital ratio of 10.0% as of such date under the Advanced A'

## cc-h-001
question=What was JPMorgan's Standardized CET1 capital ratio at year-end 2023?
raw_tokens=['what', 'was', 'jpmorgan', 's', 'standardized', 'cet1', 'capital', 'ratio', 'at', 'year', 'end', '2023']
query_embed_terms=['jpmorgan', 'standard', 'cet1', 'capit', 'ratio', 'year', 'end', '2023']
sparse_dims=8 query_values_all_ones=true
gold 0000019617-24-000225 [488083,488140) quote_tokens=['the', 'standardized', 'and', 'advanced', 'cet1', 'ratios', 'were', 'both', '15', '0']
gold_quote_has_query_terms=['cet1']
gold_quote_missing_query_terms=['jpmorgan', 'standard', 'capit', 'ratio', 'year', 'end', '2023']
gold_chunk [487323,489201) chars=1878 tokens=300 section='Item 7'
gold_chunk_has_query_terms=['cet1', 'end', '2023']
gold_chunk_missing_query_terms=['jpmorgan', 'standard', 'capit', 'ratio', 'year']
gold_chunk_has_rare_tokens=['cet1', 'slr']
gold_chunk_snippet='• Firmwide average loans of $1.2 trillion were up 13%, predominantly driven by higher loans in CCB and CB, primarily as a result of First Republic. • Firmwide a'
gold_chunk_bm25_rank=97
bm25_top5:
  1. score=31.2844 ticker=JPM doc_id=0001628280-26-008131 [1550629,1552278) gold=False 'Note: The table above is as defined by the regulations issued by the Federal Reserve, OCC and FDIC and to which the Firm and JPMorgan Chase Bank, N.A. are subje'
  2. score=30.7809 ticker=JPM doc_id=0000019617-24-000225 [1613318,1615065) gold=False 'Note: The table above is as defined by the regulations issued by the Federal Reserve, OCC and FDIC and to which the Firm and JPMorgan Chase Bank, N.A. are subje'
  3. score=30.7723 ticker=JPM doc_id=0000019617-25-000270 [1576405,1578096) gold=False 'Note: The table above is as defined by the regulations issued by the Federal Reserve, OCC and FDIC and to which the Firm and JPMorgan Chase Bank, N.A. are subje'
  4. score=30.7408 ticker=C doc_id=0000831001-24-000033 [506434,507793) gold=False 'res to a CET1 Capital ratio of 12.2% as of December 31, 2022, relative to a required regulatory CET1 Capital ratio of 10.0% as of such date under the Advanced A'
  5. score=30.6928 ticker=C doc_id=0000831001-25-000067 [521820,523000) gold=False 'III Advanced Approaches was 12.1% as of December 31, 2024, 12.2% as of September 30, 2024, and 12.1% as of December 31, 2023, relative to a required regulatory '

