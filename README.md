This repository implements an automated research-paper ingestion pipeline that:
      1. Fetches recent papers from specified target journals via OpenAlex
      2. Processes each new paper to produce structured metadata (paper_id, title, authors, year, venue, full abstract), extracts keywords (YAKE + embedding rerank), generates an abstractive summary (LLM), and classifies paper type (LLM)
      3. Appends only new papers to a Google Sheet 
      4. Runs on a weekly schedule via GitHub Actions

The system is split into a driver (ingest.py) that handles sheet I/O and incremental logic, a processing module (pipeline.py) that implements fetching and process_paper_by_meta, and a workflow weekly-ingest.yml that automates weekly execution. The pipeline batches and rate-limits Google Sheets writes, uses exponential backoff for reliability, and persistently stores one row per paper in the spreadsheet.

STEPS OF EXECUTION:
A. Files required (placed at repo root):
  a. ingest.py - ingestion driver (sheet-only incremental runner)
  b. pipeline.py - fetch + process functions exposing:
      i. fetch_openalex_for_journals(years_back=5) -> List[dict]
      ii. process_paper_by_meta(meta) -> Dict[str,Any] with keys: 
          1. paper_id
          2. title
          3. authors
          4. year
          5. venue
          6. abstract_full
          7. keywords
          8. abstract_summary
          9. paper_type
          10. link
  c. .github/workflows/weekly-ingest.yml - GitHub Actions workflow

B. Local one-off run (developer testing):
  a. Create Python venv and install dependencies:
      i. python -m venv venv
      ii. For macOS / Linux:
              source venv/bin/activate
      iii. For Windows PowerShell
              .\venv\Scripts\Activate.ps1
b. pip install -r requirements.txt
c. Prepare Google service account JSON file (download from GCP) as sa.json.
d. Export environment variables (replace placeholders):
i. For macOS / Linux
export SHEET_ID="YOUR_GOOGLE_SHEET_ID"
2. export GCP_SA_JSON="$(cat ./sa.json)"
ii. For Windows PowerShell
# $env:SHEET_ID = "YOUR_GOOGLE_SHEET_ID"
# $env:GCP_SA_JSON = Get-Content -Raw .\sa.json
e. Ensure ingest.py and pipeline.py are at repo root and that ingest.py imports the processing module by the filename used (no additional edits required if files match).

f.	Run the ingestion driver, python ingest.py. The script will: 
i.	Read existing paper_ids from the sheet
ii.	Fetch recent papers from OpenAlex
iii.	Compute new_ids
iv.	Run process_paper_by_meta for each new paper
v.	Append processed rows to the Google Sheet.
g.	GitHub Actions (automated weekly) - setup & manual run
h.	Commit ingest.py, pipeline.py, and .github/workflows/weekly-ingest.yml to your repository’s default branch.
i.	Add repository secrets (Settings -> Secrets -> Actions):
i.	SHEET_ID -> the Google Sheet ID (from URL)
ii.	GCP_SA_JSON -> the full service-account JSON content (paste the JSON as the secret value)
iii.	Ensure the Google Sheet is shared with the service account email (the client_email inside the JSON) with Editor permissions.
j.	From the GitHub repo -> Actions -> select Weekly paper ingestion workflow -> click “Run workflow” to test immediately, or wait for the scheduled weekly run.

C.	What the workflow executes when running:
a.	Checks out the repo, sets up Python, installs requirements.txt, and runs python ingest.py
b.	The same ingest logic used locally executes on the runner and appends only new rows to the Google Sheet.


D.	Success verification through sheets:
a.	After run completes, open the target Google Sheet and verify new rows appended with columns:
i.	paper_id 
ii.	title
iii.	authors
iv.	year
v.	venue 
vi.	abstract_full
vii.	keywords
viii.	abstract_summary
ix.	paper_type
x.	link


CODE SUMMARIES:
A.	ingest.py:
a.	Purpose: This script performs incremental ingestion of research papers into a Google Sheet. It ensures that only new papers (not already stored in the sheet) are processed and appended.
b.	Authentication and Setup: The script authenticates with Google Sheets API using a Google Cloud service account. It reads required credentials from environment variables:
i.	SHEET_ID - ID of the target Google Sheet
ii.	GCP_SA_JSON - service account JSON credentials
iii.	After authentication, it opens the specified worksheet.
c.	Reading Existing Paper IDs: The script reads the existing paper IDs from the sheet to avoid duplicates.
i.	Steps:
1.	Identify the column containing paper_id
2.	Extract all existing IDs
3.	Normalize OpenAlex IDs into a consistent format
4.	Store them in a set for fast lookup
d.	Fetching Papers from OpenAlex: The script calls:
i.	fetch_openalex_for_journals()
This retrieves metadata of research papers from OpenAlex for selected journals. Each record is normalized and indexed using its paper_id.
e.	Detecting New Papers: The script compares Fetched paper IDs with IDs already stored in the sheet. Only papers not already in the sheet are considered new papers and selected for processing.
f.	Processing Paper Metadata: Each new paper is processed using “process_paper_by_meta(meta)”. This function extracts and generates structured information and the processed data is converted into a row format matching predefined sheet columns.
g.	Dynamic Batch Optimization: To prevent Google Sheets limits from being exceeded, the script:
i.	Estimates average row size
ii.	Dynamically adjusts the maximum rows per batch
iii.	Ensures the request payload stays below the 2 MB API limit
h.	Rate Limiting and Quota Handling: Google Sheets allows ~60 writes per minute. The script: 
i.	Limits requests to 50 writes per minute
ii.	Adds automatic waiting between calls
iii.	Uses exponential backoff retries if API errors occur
iv.	This prevents quota violations.
i.	Batch Appending Data: Processed rows are accumulated into batches and then appended to the sheet using ws.append_rows(). Batching improves performance and reduces API calls.
j.	Final Output: After processing all new papers:
i.	Remaining rows are appended 
ii.	The script prints the total number of papers processed
iii.	Execution time is displayed


B.	pipeline.py:
a.	Purpose: Provides a combined fetch + process pipeline for research papers from OpenAlex, exposing two stable functions used by the ingestion driver:
i.	fetch_openalex_for_journals(...) 
ii.	process_paper_by_meta(meta)
b.	Model & library initialization: 
i.	Loads embedding model: SentenceTransformer("all-MiniLM-L6-v2"). 
ii.	Loads a large Qwen causal LM (Qwen/Qwen2.5-7B-Instruct) and its tokenizer.
iii.	Imports utilities: requests, pandas, YAKE (keyword extraction), Hugging Face transformers, PyTorch, and helper libs.
c.	OpenAlex fetch utilities: 
i.	Base endpoint: https://api.openalex.org/works. 
ii.	Paging and cursor-based fetching (200 items/page) with OPENALEX_RATE_SLEEP between requests. 
iii.	Supports filtering by ISSN or display name and limits by recent years (YEARS_BACK, default 5).
d.	Abstract reconstruction: _reconstruct_abstract_from_inverted_index(inv_idx) rebuilds full abstracts when OpenAlex returns an inverted-index instead of plain text.
e.	Per-journal fetcher: fetch_openalex_for_journal(journal_name, ...): 
i.	Builds OpenAlex filter (ISSN or display_name + start date). 
ii.	Iterates through pages, extracts fields (id, title, authors, year, abstract, venue, doi). 
iii.	Handles inverted-index abstracts and deduplicates results.
f.	Multi-journal aggregation: fetch_openalex_for_journals_impl(years_back): 
i.	Calls per-journal fetch for allowed journals (set ALLOWED_JOURNALS). 
ii.	Normalizes paper IDs, de-duplicates across journals, and returns a list of unique paper metadata dicts.
g.	Keyword extraction: extract_keywords(paper, top_k=5): 
i.	Uses existing keywords if present. 
ii.	Otherwise extracts candidates with YAKE from the abstract. 
iii.	Ranks and filters candidates, re-ranks using embedding similarity against the full abstract (SentenceTransformer), and returns top keywords (list + semicolon string).
h.	Abstract summarization (Qwen): summarize_abstract_with_qwen(abstract, max_new_tokens=64): 
i.	Builds a system + user prompt instructing Qwen to output 1–2 concise academic sentences (research question, method, contribution). 
ii.	Runs generation and post-processes to trim and clean the summary; robust to failures (returns empty string on error).
i.	Paper-type inference (Qwen): infer_paper_type(title, abstract): 
i.	Prompts Qwen to classify into categories (review, qualitative, quantitative, case_study, essay, research, unclear). 
ii.	Parses model output and maps common labels into a canonical set; falls back to "unclear" on errors.
j.	Row/record processing: process_paper_by_meta(meta): 
i.	Normalizes paper_id, extracts title, comma-joined authors, year, venue, and the raw abstract_full. 
ii.	Runs keyword extraction, Qwen summarization, and type inference. 
iii.	Produces a dict with the output keys expected by the sheet driver: 
1.	paper_id
2.	title
3.	authors
4.	year
5.	venue 
6.	abstract_full
7.	keywords
8.	abstract_summary, paper_type
9.	link.
