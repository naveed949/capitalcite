# 0003. Parse and three chunkers

Date: 2026-10-16
Status: Accepted

Issue #3 turns stored HTML and PDF into clean text, then into chunks a later
retriever can cite. This record is why the parser, the three strategies, and
the citation fields look the way they do.

## A chunk is a cited span, not a bag of tokens

Retrieval will return text that an answer must point back to a filing. The
unit we store therefore has to name the filing, the part of the filing, and
a locator inside that part.

Each chunk carries:

- `doc_id`. The accession number. It is unique per EDGAR submission and
  already lives on the ingest sidecar.
- `section`. The 10-K Item at the start of the chunk (`Item 7`, `Item 8`),
  or `body` for front matter.
- `start` and `end`. Half-open character range in the cleaned document
  text. `document.text[start:end]` is the original span. A citation is
  checkable when that slice exists: the stored `text` is either exactly
  that slice, or a documented `header_prefix` (a repeated markdown table
  header plus separator) prepended to it. Without a stored span, a later
  retriever cannot prove a quote came from the filing rather than from a
  join or a strip.
- `offset`. Character index of the first non-whitespace character in that
  original span. HTML has no reliable printed page, so offset is the
  highlight cursor every strategy can fill. It is not a substitute for
  `start`/`end`; packing used to join units with `"\\n\\n"` and strip
  leading whitespace without moving offset, so `text` was not a document
  slice and offset did not name the first visible character.
- `page`. 1-based PDF page when the source is a PDF. Absent for HTML.
- `source_url`. The archive URL from `metadata.json`.

Ticker, form, period, and accession ride along so #4 can filter without
joining back to the sidecar. They are not substitutes for the citation
fields.

## Why not one fixed window

A 10-K is a book with labeled chapters. Item 7 is MD&A. Item 8 is the
financial statements. A question like "what was CET1 at year-end" is almost
always answered inside one Item, often next to a table.

A fixed window of 512 tokens with 64 tokens of overlap is simple and
reproducible. It is also blind. It will cut a table in half. It will start
in Item 7 and end in Item 8. The overlap exists so a sentence that sits on
a cut is not lost. It does not restore the Item boundary the cut destroyed.

Recursive splitting tries the same size budget but prefers large
separators first (`\\n\\n`, then `\\n`, then `. `, then space). It keeps
paragraphs together more often than a raw character slice. It still does
not know what an Item is.

Section-aware splitting cuts on Item headings first. An Item that fits in
the budget becomes one chunk. An Item that does not is then split with the
recursive splitter, and every piece keeps that Item name.

Markdown table blocks are atomic. Consecutive lines that start with `|`
are one table, including the header and separator. Recursive splitting
does not cut on `\\n` inside that block. A table that fits in the window
stays one unit. A table that exceeds the window is split on row
boundaries. Every piece after the first repeats the header row and the
separator so the piece is still a valid markdown table: header, separator,
data rows, and no blank lines between rows. `start`/`end` name the
original rows in that piece. `header_prefix` holds the prepended header
and separator. The first piece has an empty prefix because those lines
already sit in the span.

Default size is 512 tokens and overlap is 64. Those numbers match the
README's `fixed-512` row so later evals compare the same budget. 512 is
large enough for a short Item plus a small table, and small enough that a
dense retriever is not asked to embed a whole MD&A. Overlap of about
one-eighth of the window is a common starting point. It is a knob, not a
finding. Issue #6 will say which pair wins on the golden set.

A token here is four characters, rounded up. `mean_tokens` in `stats.json`
is the mean of that heuristic over the chunks. It is an approximate budget
figure, not an embedding-model token count. Issue #4 picks the embedding
model. Counting with `cl100k_base` now would publish mean-token stats
against the wrong vocabulary.

## Why the parser emits markdown, not a public block tree

Two shapes were on the table.

A typed block tree (`Heading`, `Paragraph`, `Table`, `PageBreak`) makes
table integrity a type invariant. Callers then have to walk the tree.
`index` in #4 would need that walk too.

A cleaned markdown string plus section and page spans hides the HTML walk.
Chunkers and the later retriever see text. Tables still become markdown
inside the parser. The splitter keeps each markdown table block intact, or
repeats the header when a table is larger than the window. That is the
shape we shipped. The block walk is an implementation detail of
`html_to_document`.

HTML is parsed with BeautifulSoup's `html.parser`. We do not add
LangChain, unstructured, or LlamaIndex. Those libraries would own the
chunk type and leak into tests. PDF text comes from pypdf. PDF table
structure is not recovered. The table-preservation tests are HTML.

`src/capitalcite/ingest/parse.py` stays CLI flag parsing. Document parsing
lives in `capitalcite.parse`.

## Config picks the strategy

`CHUNKER`, `CHUNK_SIZE`, and `CHUNK_OVERLAP` are settings. `capitalcite
chunk --chunker section --size 512 --overlap 64` overrides them. The
registry is `CHUNKERS` keyed by `ChunkerName`. Adding a fourth strategy is
one function plus one enum member.

Stats (count, mean tokens) are logged per document and per run, and written
to `stats.json` next to `chunks.jsonl`. Mean tokens uses the four-character
heuristic, so treat the number as approximate until #4.

## Atomic writes

`FilingStore.write` used `Path.write_bytes`. A crash mid-write can leave a
truncated 10-K that the next ingest will treat as complete. Writes now go
to a temp file in the same directory and `os.replace` onto the destination.
Parsed `document.md`, `chunks.jsonl`, and `stats.json` use the same helper.

## Alternatives considered

| Choice | Why not |
|---|---|
| LangChain text splitters | Own the chunk type. Pull a stack we do not serve yet. |
| tiktoken `cl100k_base` | Honest only after #4 picks that vocabulary. |
| One public `Block` union | Deeper for the parser, shallower for every later caller. |
| Page-only locators | HTML 10-Ks do not have a stable printed page. |
| Chunk only at index time, persist nothing | `verify.sh` and #4 then re-parse on every run. Derived files are cheap. |
| Filter TOC by "first Item 1 only" | Leader dots plus a trailing page number already mark TOC lines. |
