BM25 diagnostic
collection=filings-d7234fd1ff33b612d840c7f0590b8ada30afc7ff326c2fb659f461ab902c2c58 points probe_k=2000
chunker=section embedder=fastembed:BAAI/bge-small-en-v1.5
idf_modifier=IDF
embed_query builds the dense vector (BGE may prefix that vector) and pairs term_counts of the raw query as the sparse vector. embed_documents pairs term_counts of chunk.text. BM25 search sends only the sparse vector.
tokenizer=[a-z0-9]+ after lowercasing. CET1 -> cet1.
rare_token_examples=['cet1', 'wells', 'fargo', 'slr', '2025']

## cc-h-013
question=What was JPMorgan's Standardized CET1 capital ratio at year-end 2024?
query_tokens=['what', 'was', 'jpmorgan', 's', 'standardized', 'cet1', 'capital', 'ratio', 'at', 'year', 'end', '2024']
query_term_counts=what=1, was=1, jpmorgan=1, s=1, standardized=1, cet1=1, capital=1, ratio=1, at=1, year=1, end=1, 2024=1
sparse_dims=12 (indices hashed, values are raw counts)
gold 0000019617-25-000270 [487805,487867) quote_tokens=['the', 'standardized', 'and', 'advanced', 'cet1', 'ratios', 'were', '15', '7', 'and', '15', '8']
gold_quote_has_query_tokens=['standardized', 'cet1']
gold_quote_missing_query_tokens=['what', 'was', 'jpmorgan', 's', 'capital', 'ratio', 'at', 'year', 'end', '2024']
gold_chunk [485869,487907) chars=2038 tokens=339 section='Item 7'
gold_chunk_has_query_tokens=['was', 's', 'standardized', 'cet1', 'capital', 'ratio', 'at', 'year', '2024']
gold_chunk_missing_query_tokens=['what', 'jpmorgan', 'end']
gold_chunk_has_rare_tokens=['cet1', 'slr']
gold_chunk_snippet='was $10.7 billion, reflecting $8.6 billion of net charge-offs and a net addition to the allowance for credit losses of $2.0 billion. Net charge-offs increased b'
gold_chunk [487651,489193) chars=1542 tokens=235 section='Item 7'
gold_chunk_has_query_tokens=['was', 'jpmorgan', 's', 'standardized', 'cet1', 'capital', 'at', 'year', 'end', '2024']
gold_chunk_missing_query_tokens=['what', 'ratio']
gold_chunk_has_rare_tokens=['cet1', 'slr']
gold_chunk_snippet='Refer to Liquidity Risk Management on pages 108–115 for additional information. Selected capital and other metrics • CET1 capital was $275.5 billion, and the St'
gold_chunk_bm25_rank=172
bm25_top5:
  1. score=162.6760 doc_id=0000831001-24-000033 [505644,507386) gold=False '35 Common Equity Tier 1 Capital Ratio Citi’s Common Equity Tier 1 (CET1) Capital ratio under the Basel III Standardized Approach was 13.4% as of December 31, 20'
  2. score=137.0709 doc_id=0000831001-25-000067 [521284,522998) gold=False '37 Common Equity Tier 1 Capital Ratio Citi’s Common Equity Tier 1 (CET1) Capital ratio under the Basel III Standardized Approach was 13.6% as of December 31, 20'
  3. score=120.8307 doc_id=0000831001-26-000011 [504163,505933) gold=False '(1) Commencing January 1, 2025, the capital effects resulting from adoption of the current expected credit losses (CECL) methodology have been fully reflected i'
  4. score=104.0551 doc_id=0000019617-25-000270 [678041,680036) gold=False '| | | | | | | | --- | --- | --- | --- | --- | --- | | Year ended December 31, (in millions) | 2024 | | | | | | Standardized/Advanced CET1 capital at December 31'
  5. score=102.9800 doc_id=0001628280-26-008131 [656572,658576) gold=False '| | | | | | | | --- | --- | --- | --- | --- | --- | | Year ended December 31, (in millions) | 2025 | | | | | | Standardized/Advanced CET1 capital at December 31'

## cc-h-063
question=What CET1 ratio did Wells Fargo report under the Standardized Approach at year-end 2025?
query_tokens=['what', 'cet1', 'ratio', 'did', 'wells', 'fargo', 'report', 'under', 'the', 'standardized', 'approach', 'at', 'year', 'end', '2025']
query_term_counts=what=1, cet1=1, ratio=1, did=1, wells=1, fargo=1, report=1, under=1, the=1, standardized=1, approach=1, at=1, year=1, end=1, 2025=1
sparse_dims=15 (indices hashed, values are raw counts)
gold 0000072971-26-000133 [228593,228647) quote_tokens=['cet1', 'ratio', 'was', '10', '61', 'under', 'the', 'standardized', 'approach']
gold_quote_has_query_tokens=['cet1', 'ratio', 'under', 'the', 'standardized', 'approach']
gold_quote_missing_query_tokens=['what', 'did', 'wells', 'fargo', 'report', 'at', 'year', 'end', '2025']
gold_chunk [227583,229470) chars=1887 tokens=282 section='Item 7'
gold_chunk_has_query_tokens=['cet1', 'ratio', 'report', 'under', 'the', 'standardized', 'approach', 'at', '2025']
gold_chunk_missing_query_tokens=['what', 'did', 'wells', 'fargo', 'year', 'end']
gold_chunk_has_rare_tokens=['cet1', '2025']
gold_chunk_snippet='Financial Performance In 2025, we generated $21.3 billion of net income and diluted earnings per share (EPS) of $6.26, compared with $19.7 billion of net income'
gold_chunk_bm25_rank=212
bm25_top5:
  1. score=166.0909 doc_id=0000831001-24-000033 [505644,507386) gold=False '35 Common Equity Tier 1 Capital Ratio Citi’s Common Equity Tier 1 (CET1) Capital ratio under the Basel III Standardized Approach was 13.4% as of December 31, 20'
  2. score=137.1444 doc_id=0000831001-25-000067 [521284,522998) gold=False '37 Common Equity Tier 1 Capital Ratio Citi’s Common Equity Tier 1 (CET1) Capital ratio under the Basel III Standardized Approach was 13.6% as of December 31, 20'
  3. score=117.4414 doc_id=0000831001-26-000011 [504163,505933) gold=False '(1) Commencing January 1, 2025, the capital effects resulting from adoption of the current expected credit losses (CECL) methodology have been fully reflected i'
  4. score=96.9615 doc_id=0000070858-26-000157 [628079,629661) gold=False "The Corporation's depository institution subsidiaries are also subject to the Prompt Corrective Action (PCA) framework. The Corporation and its primary affiliat"
  5. score=92.9310 doc_id=0000070858-25-000139 [640862,642618) gold=False 'Minimum Capital Requirements In order to avoid restrictions on capital distributions and discretionary bonus payments to executive officers, the Corporation mus'

## cc-h-001
question=What was JPMorgan's Standardized CET1 capital ratio at year-end 2023?
query_tokens=['what', 'was', 'jpmorgan', 's', 'standardized', 'cet1', 'capital', 'ratio', 'at', 'year', 'end', '2023']
query_term_counts=what=1, was=1, jpmorgan=1, s=1, standardized=1, cet1=1, capital=1, ratio=1, at=1, year=1, end=1, 2023=1
sparse_dims=12 (indices hashed, values are raw counts)
gold 0000019617-24-000225 [488083,488140) quote_tokens=['the', 'standardized', 'and', 'advanced', 'cet1', 'ratios', 'were', 'both', '15', '0']
gold_quote_has_query_tokens=['standardized', 'cet1']
gold_quote_missing_query_tokens=['what', 'was', 'jpmorgan', 's', 'capital', 'ratio', 'at', 'year', 'end', '2023']
gold_chunk [487188,489201) chars=2013 tokens=308 section='Item 7'
gold_chunk_has_query_tokens=['was', 'jpmorgan', 's', 'standardized', 'cet1', 'capital', 'at', 'end', '2023']
gold_chunk_missing_query_tokens=['what', 'ratio', 'year']
gold_chunk_has_rare_tokens=['cet1', 'slr']
gold_chunk_snippet='| | | | | | | | | | | --- | --- | --- | --- | --- | --- | --- | --- | --- | | JPMorgan Chase & Co./2023 Form 10-K | | 49 | | | | | | | • Firmwide average loans '
gold_chunk_bm25_rank=271
bm25_top5:
  1. score=168.3244 doc_id=0000831001-24-000033 [505644,507386) gold=False '35 Common Equity Tier 1 Capital Ratio Citi’s Common Equity Tier 1 (CET1) Capital ratio under the Basel III Standardized Approach was 13.4% as of December 31, 20'
  2. score=135.4609 doc_id=0000831001-25-000067 [521284,522998) gold=False '37 Common Equity Tier 1 Capital Ratio Citi’s Common Equity Tier 1 (CET1) Capital ratio under the Basel III Standardized Approach was 13.6% as of December 31, 20'
  3. score=119.7556 doc_id=0000831001-26-000011 [504163,505933) gold=False '(1) Commencing January 1, 2025, the capital effects resulting from adoption of the current expected credit losses (CECL) methodology have been fully reflected i'
  4. score=103.3865 doc_id=0000019617-24-000225 [705189,707177) gold=False '| | | | | | | | --- | --- | --- | --- | --- | --- | | Year ended December 31, (in millions) | 2023 | | | | | | Standardized/Advanced CET1 capital at December 31'
  5. score=102.4451 doc_id=0000019617-25-000270 [678041,680036) gold=False '| | | | | | | | --- | --- | --- | --- | --- | --- | | Year ended December 31, (in millions) | 2024 | | | | | | Standardized/Advanced CET1 capital at December 31'


BM25 diagnostic
collection=filings-f06b8a8c61c8f411083f7885c67579c31c839c416e82711cfda38e53007052ce points probe_k=2000
chunker=fixed embedder=fastembed:BAAI/bge-small-en-v1.5
idf_modifier=IDF
embed_query builds the dense vector (BGE may prefix that vector) and pairs term_counts of the raw query as the sparse vector. embed_documents pairs term_counts of chunk.text. BM25 search sends only the sparse vector.
tokenizer=[a-z0-9]+ after lowercasing. CET1 -> cet1.
rare_token_examples=['cet1', 'wells', 'fargo', 'slr', '2025']

## cc-h-013
question=What was JPMorgan's Standardized CET1 capital ratio at year-end 2024?
query_tokens=['what', 'was', 'jpmorgan', 's', 'standardized', 'cet1', 'capital', 'ratio', 'at', 'year', 'end', '2024']
query_term_counts=what=1, was=1, jpmorgan=1, s=1, standardized=1, cet1=1, capital=1, ratio=1, at=1, year=1, end=1, 2024=1
sparse_dims=12 (indices hashed, values are raw counts)
gold 0000019617-25-000270 [487805,487867) quote_tokens=['the', 'standardized', 'and', 'advanced', 'cet1', 'ratios', 'were', '15', '7', 'and', '15', '8']
gold_quote_has_query_tokens=['standardized', 'cet1']
gold_quote_missing_query_tokens=['what', 'was', 'jpmorgan', 's', 'capital', 'ratio', 'at', 'year', 'end', '2024']
gold_chunk [487080,488906) chars=1826 tokens=294 section='Item 7'
gold_chunk_has_query_tokens=['was', 's', 'standardized', 'cet1', 'capital', 'at', 'end', '2024']
gold_chunk_missing_query_tokens=['what', 'jpmorgan', 'ratio', 'year']
gold_chunk_has_rare_tokens=['cet1', 'slr']
gold_chunk_snippet='6–136 and pages 120–125, respectively, for additional information. • Firmwide average loans of $1.3 trillion were up 6%, driven by higher loans across the lines'
gold_chunk_bm25_rank=389
bm25_top5:
  1. score=176.5575 doc_id=0000831001-25-000067 [520028,522076) gold=False '(1) Citi’s regulatory capital ratios and components reflect certain deferrals based on the modified regulatory capital transition provision related to the CECL '
  2. score=170.3431 doc_id=0000831001-24-000033 [504642,506690) gold=False '(1) Citi’s regulatory capital ratios and components reflect certain deferrals based on the modified regulatory capital transition provision related to the CECL '
  3. score=158.8693 doc_id=0000831001-26-000011 [504162,506210) gold=False '(1) Commencing January 1, 2025, the capital effects resulting from adoption of the current expected credit losses (CECL) methodology have been fully reflected i'
  4. score=120.5368 doc_id=0000831001-24-000033 [506434,507793) gold=False 'res to a CET1 Capital ratio of 12.2% as of December 31, 2022, relative to a required regulatory CET1 Capital ratio of 10.0% as of such date under the Advanced A'
  5. score=114.5052 doc_id=0000019617-25-000270 [678041,680036) gold=False '| | | | | | | | --- | --- | --- | --- | --- | --- | | Year ended December 31, (in millions) | 2024 | | | | | | Standardized/Advanced CET1 capital at December 31'

## cc-h-063
question=What CET1 ratio did Wells Fargo report under the Standardized Approach at year-end 2025?
query_tokens=['what', 'cet1', 'ratio', 'did', 'wells', 'fargo', 'report', 'under', 'the', 'standardized', 'approach', 'at', 'year', 'end', '2025']
query_term_counts=what=1, cet1=1, ratio=1, did=1, wells=1, fargo=1, report=1, under=1, the=1, standardized=1, approach=1, at=1, year=1, end=1, 2025=1
sparse_dims=15 (indices hashed, values are raw counts)
gold 0000072971-26-000133 [228593,228647) quote_tokens=['cet1', 'ratio', 'was', '10', '61', 'under', 'the', 'standardized', 'approach']
gold_quote_has_query_tokens=['cet1', 'ratio', 'under', 'the', 'standardized', 'approach']
gold_quote_missing_query_tokens=['what', 'did', 'wells', 'fargo', 'report', 'at', 'year', 'end', '2025']
gold_chunk [226962,229010) chars=2048 tokens=314 section='Item 7'
gold_chunk_has_query_tokens=['cet1', 'ratio', 'wells', 'fargo', 'under', 'the', 'standardized', 'approach', 'at', '2025']
gold_chunk_missing_query_tokens=['what', 'did', 'report', 'year', 'end']
gold_chunk_has_rare_tokens=['cet1', 'wells', 'fargo', '2025']
gold_chunk_snippet='Wells Fargo & Company is a leading financial services company that has approximately $2.1 trillion in assets. We provide a diversified set of banking, investmen'
gold_chunk_bm25_rank=118
bm25_top5:
  1. score=162.9509 doc_id=0000831001-26-000011 [504162,506210) gold=False '(1) Commencing January 1, 2025, the capital effects resulting from adoption of the current expected credit losses (CECL) methodology have been fully reflected i'
  2. score=156.9009 doc_id=0000831001-24-000033 [504642,506690) gold=False '(1) Citi’s regulatory capital ratios and components reflect certain deferrals based on the modified regulatory capital transition provision related to the CECL '
  3. score=154.4577 doc_id=0000831001-25-000067 [520028,522076) gold=False '(1) Citi’s regulatory capital ratios and components reflect certain deferrals based on the modified regulatory capital transition provision related to the CECL '
  4. score=135.4385 doc_id=0000070858-26-000157 [628715,630763) gold=False 'e Total capital ratio under the Standardized approach. Minimum Capital Requirements In order to avoid restrictions on capital distributions and discretionary bo'
  5. score=124.3228 doc_id=0000831001-24-000033 [506434,507793) gold=False 'res to a CET1 Capital ratio of 12.2% as of December 31, 2022, relative to a required regulatory CET1 Capital ratio of 10.0% as of such date under the Advanced A'

## cc-h-001
question=What was JPMorgan's Standardized CET1 capital ratio at year-end 2023?
query_tokens=['what', 'was', 'jpmorgan', 's', 'standardized', 'cet1', 'capital', 'ratio', 'at', 'year', 'end', '2023']
query_term_counts=what=1, was=1, jpmorgan=1, s=1, standardized=1, cet1=1, capital=1, ratio=1, at=1, year=1, end=1, 2023=1
sparse_dims=12 (indices hashed, values are raw counts)
gold 0000019617-24-000225 [488083,488140) quote_tokens=['the', 'standardized', 'and', 'advanced', 'cet1', 'ratios', 'were', 'both', '15', '0']
gold_quote_has_query_tokens=['standardized', 'cet1']
gold_quote_missing_query_tokens=['what', 'was', 'jpmorgan', 's', 'capital', 'ratio', 'at', 'year', 'end', '2023']
gold_chunk [487323,489201) chars=1878 tokens=300 section='Item 7'
gold_chunk_has_query_tokens=['was', 's', 'standardized', 'cet1', 'capital', 'at', 'end', '2023']
gold_chunk_missing_query_tokens=['what', 'jpmorgan', 'ratio', 'year']
gold_chunk_has_rare_tokens=['cet1', 'slr']
gold_chunk_snippet='• Firmwide average loans of $1.2 trillion were up 13%, predominantly driven by higher loans in CCB and CB, primarily as a result of First Republic. • Firmwide a'
gold_chunk_bm25_rank=370
bm25_top5:
  1. score=177.0724 doc_id=0000831001-24-000033 [504642,506690) gold=False '(1) Citi’s regulatory capital ratios and components reflect certain deferrals based on the modified regulatory capital transition provision related to the CECL '
  2. score=170.0234 doc_id=0000831001-25-000067 [520028,522076) gold=False '(1) Citi’s regulatory capital ratios and components reflect certain deferrals based on the modified regulatory capital transition provision related to the CECL '
  3. score=156.3615 doc_id=0000831001-26-000011 [504162,506210) gold=False '(1) Commencing January 1, 2025, the capital effects resulting from adoption of the current expected credit losses (CECL) methodology have been fully reflected i'
  4. score=122.7800 doc_id=0000831001-24-000033 [506434,507793) gold=False 'res to a CET1 Capital ratio of 12.2% as of December 31, 2022, relative to a required regulatory CET1 Capital ratio of 10.0% as of such date under the Advanced A'
  5. score=113.8435 doc_id=0000019617-24-000225 [705189,707177) gold=False '| | | | | | | | --- | --- | --- | --- | --- | --- | | Year ended December 31, (in millions) | 2023 | | | | | | Standardized/Advanced CET1 capital at December 31'

