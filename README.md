This repository implements an automated research-paper ingestion pipeline that,

- Fetches recent papers from specified target journals via OpenAlex
- Processes each new paper to produce structured metadata (paper_id, title, authors, year, venue, full abstract), extracts keywords (YAKE + embedding rerank), generates an abstractive summary (LLM), and classifies paper type (LLM)
- Appends only new papers to a Google Sheet
- Runs on a weekly schedule via GitHub Actions

The system is split into a driver **(ingest.py)** that handles sheet I/O and incremental logic, a processing module **(pipeline.py)** that implements fetching and process_paper_by_meta, and a workflow **weekly-ingest.yml** that automates weekly execution. The pipeline batches and rate-limits Google Sheets writes, uses exponential backoff for reliability, and persistently stores one row per paper in the spreadsheet.

STEPS OF EXECUTION:

- Files required (placed at repo root):
  - ingest.py - ingestion driver (sheet-only incremental runner)
  - pipeline.py - fetch + process functions exposing:
    - fetch_openalex_for_journals(years_back=5) -> List\[dict\]
    - process_paper_by_meta(meta) -> Dict\[str,Any\] with keys:
      - paper_id
      - title
      - authors
      - year
      - venue
      - abstract_full
      - keywords
      - abstract_summary
      - paper_type
      - link   
  - .github/workflows/weekly-ingest.yml - GitHub Actions workflow

  
- Local one-off run (developer testing):
  - Create Python venv and install dependencies:
    - python -m venv venv
    - For macOS / Linux:
      - source venv/bin/activate
    - For Windows PowerShell
      - .\\venv\\Scripts\\Activate.ps1
  - pip install -r requirements.txt
  - Prepare Google service account JSON file (download from GCP) as sa.json.
  - Export environment variables (replace placeholders):
    - For macOS / Linux
      - export SHEET_ID="YOUR_GOOGLE_SHEET_ID"
      - export GCP_SA_JSON="\$(cat ./sa.json)"
    - For Windows PowerShell
      - \# \$env:SHEET_ID = "YOUR_GOOGLE_SHEET_ID"
      - \# \$env:GCP_SA_JSON = Get-Content -Raw .\\sa.json
  - Ensure ingest.py and pipeline.py are at repo root and that ingest.py imports the processing module by the filename used (no additional edits required if files match).
  - Run the ingestion driver, python ingest.py. The script will:
    - Read existing paper_ids from the sheet
    - Fetch recent papers from OpenAlex
    - Compute new_ids
    - Run process_paper_by_meta for each new paper
    - Append processed rows to the Google Sheet.
  - GitHub Actions (automated weekly) - setup & manual run
  - Commit ingest.py, pipeline.py, and .github/workflows/weekly-ingest.yml to your repository's default branch.
  - Add repository secrets (Settings -> Secrets -> Actions):
    - SHEET_ID -> the Google Sheet ID (from URL)
    - GCP_SA_JSON -> the full service-account JSON content (paste the JSON as the secret value)
    - Ensure the Google Sheet is shared with the service account email (the client_email inside the JSON) with Editor permissions.
  - From the GitHub repo -> Actions -> select Weekly paper ingestion workflow -> click "Run workflow" to test immediately, or wait for the scheduled weekly run.
- What the workflow executes when running:
  - Checks out the repo, sets up Python, installs requirements.txt, and runs python ingest.py
  - The same ingest logic used locally executes on the runner and appends only new rows to the Google Sheet.

- Success verification through sheets:
  - After run completes, open the target Google Sheet and verify new rows appended with columns:
    - paper_id
    - title
    - authors
    - year
    - venue
    - abstract_full
    - keywords
    - abstract_summary
    - paper_type
    - link

CODE SUMMARIES:

- **ingest.py:**
  - Purpose: This script performs incremental ingestion of research papers into a Google Sheet. It ensures that only new papers (not already stored in the sheet) are processed and appended.
  - Authentication and Setup: The script authenticates with Google Sheets API using a Google Cloud service account. It reads required credentials from environment variables:
    - SHEET_ID - ID of the target Google Sheet
    - GCP_SA_JSON - service account JSON credentials
    - After authentication, it opens the specified worksheet.
  - Reading Existing Paper IDs: The script reads the existing paper IDs from the sheet to avoid duplicates.
    - Steps:
      - Identify the column containing paper_id
      - Extract all existing IDs
      - Normalize OpenAlex IDs into a consistent format
      - Store them in a set for fast lookup
  - Fetching Papers from OpenAlex: The script calls:
    - fetch_openalex_for_journals()

This retrieves metadata of research papers from OpenAlex for selected journals. Each record is normalized and indexed using its paper_id.

- 1. Detecting New Papers: The script compares Fetched paper IDs with IDs already stored in the sheet. Only papers not already in the sheet are considered new papers and selected for processing.
  - Processing Paper Metadata: Each new paper is processed using "process_paper_by_meta(meta)". This function extracts and generates structured information and the processed data is converted into a row format matching predefined sheet columns.
  - Dynamic Batch Optimization: To prevent Google Sheets limits from being exceeded, the script:
    - Estimates average row size
    - Dynamically adjusts the maximum rows per batch
    - Ensures the request payload stays below the 2 MB API limit
  - Rate Limiting and Quota Handling: Google Sheets allows ~60 writes per minute. The script:
    - Limits requests to 50 writes per minute
    - Adds automatic waiting between calls
    - Uses exponential backoff retries if API errors occur
    - This prevents quota violations.
  - Batch Appending Data: Processed rows are accumulated into batches and then appended to the sheet using ws.append_rows(). Batching improves performance and reduces API calls.
  - Final Output: After processing all new papers:
    - Remaining rows are appended
    - The script prints the total number of papers processed
    - Execution time is displayed  
       <br/>

- **pipeline.py:**
  - Purpose: Provides a combined fetch + process pipeline for research papers from OpenAlex, exposing two stable functions used by the ingestion driver:
    - fetch_openalex_for_journals(...)
    - process_paper_by_meta(meta)
  - Model & library initialization:
    - Loads embedding model: SentenceTransformer("all-MiniLM-L6-v2").
    - Loads a large Qwen causal LM (Qwen/Qwen2.5-7B-Instruct) and its tokenizer.
    - Imports utilities: requests, pandas, YAKE (keyword extraction), Hugging Face transformers, PyTorch, and helper libs.
  - OpenAlex fetch utilities:
    - Base endpoint: <https://api.openalex.org/works>.
    - Paging and cursor-based fetching (200 items/page) with OPENALEX_RATE_SLEEP between requests.
    - Supports filtering by ISSN or display name and limits by recent years (YEARS_BACK, default 5).
  - Abstract reconstruction: \_reconstruct_abstract_from_inverted_index(inv_idx) rebuilds full abstracts when OpenAlex returns an inverted-index instead of plain text.
  - Per-journal fetcher: fetch_openalex_for_journal(journal_name, ...):
    - Builds OpenAlex filter (ISSN or display_name + start date).
    - Iterates through pages, extracts fields (id, title, authors, year, abstract, venue, doi).
    - Handles inverted-index abstracts and deduplicates results.
  - Multi-journal aggregation: fetch_openalex_for_journals_impl(years_back):
    - Calls per-journal fetch for allowed journals (set ALLOWED_JOURNALS).
    - Normalizes paper IDs, de-duplicates across journals, and returns a list of unique paper metadata dicts.
  - Keyword extraction: extract_keywords(paper, top_k=5):
    - Uses existing keywords if present.
    - Otherwise extracts candidates with YAKE from the abstract.
    - Ranks and filters candidates, re-ranks using embedding similarity against the full abstract (SentenceTransformer), and returns top keywords (list + semicolon string).
  - Abstract summarization (Qwen): summarize_abstract_with_qwen(abstract, max_new_tokens=64):
    - Builds a system + user prompt instructing Qwen to output 1-2 concise academic sentences (research question, method, contribution).
    - Runs generation and post-processes to trim and clean the summary; robust to failures (returns empty string on error).
  - Paper-type inference (Qwen): infer_paper_type(title, abstract):
    - Prompts Qwen to classify into categories (review, qualitative, quantitative, case_study, essay, research, unclear).
    - Parses model output and maps common labels into a canonical set; falls back to "unclear" on errors.
  - Row/record processing: process_paper_by_meta(meta):
    - Normalizes paper_id, extracts title, comma-joined authors, year, venue, and the raw abstract_full.
    - Runs keyword extraction, Qwen summarization, and type inference.
    - Produces a dict with the output keys expected by the sheet driver:
      - paper_id
      - title
      - authors
      - year
      - venue
      - abstract_full
      - keywords
      - abstract_summary, paper_type
      - link.
