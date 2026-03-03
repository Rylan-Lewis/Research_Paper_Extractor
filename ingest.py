# incremental.py
"""
Sheet-only incremental ingestion driver.

- Reads existing paper IDs from the Google Sheet (ID column header 'paper_id' expected).
- Calls fetch_openalex_for_journals() from my_pipeline.py to fetch current papers.
- Normalizes IDs and computes new_ids (fetched - existing).
- Calls process_paper_by_meta(meta) only on new items.
- Appends new rows directly to Google Sheets in batches.
"""

import os
import json
import time
import random
from typing import List
from tqdm import tqdm

import gspread
from oauth2client.service_account import ServiceAccountCredentials

# Import pipeline functions (from your pipeline implementation)
from my_pipeline import fetch_openalex_for_journals, process_paper_by_meta

# --- Configuration ---
SHEET_ID_ENV = "SHEET_ID"
GCP_SA_JSON_ENV = "GCP_SA_JSON"
WORKSHEET_NAME = None  # None -> first worksheet
ID_HEADER = "paper_id"  # header name of the ID column in the sheet
FIELDNAMES = [
    "paper_id", "title", "authors", "year", "venue",
    "abstract_full", "keywords", "abstract_summary", "paper_type", "link"
]

APPEND_BATCH_SIZE = 50
APPEND_RETRIES = 5
INITIAL_BACKOFF = 1.0
MAX_BACKOFF = 30.0

# --- Helpers ---
def normalize_openalex_id(raw_id: str) -> str | None:
    if not raw_id:
        return None
    raw = str(raw_id).strip()
    if raw.startswith("https://openalex.org/") or raw.startswith("http://openalex.org/"):
        return raw.split("/")[-1]
    if raw.startswith("openalex:"):
        return raw.split(":", 1)[1]
    return raw

def gs_client_from_env(sa_env=GCP_SA_JSON_ENV):
    sa_json_str = os.environ.get(sa_env)
    if not sa_json_str:
        raise ValueError(f"{sa_env} not found in environment (set GCP_SA_JSON secret).")
    sa_json = json.loads(sa_json_str)
    scope = ["https://spreadsheets.google.com/feeds","https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(sa_json, scope)
    client = gspread.authorize(creds)
    return client

def open_worksheet(client, sheet_id: str, worksheet_name=None):
    sh = client.open_by_key(sheet_id)
    if worksheet_name:
        ws = sh.worksheet(worksheet_name)
    else:
        ws = sh.get_worksheet(0)
    return ws

def read_existing_ids_from_sheet(ws, id_header=ID_HEADER) -> set:
    # Try to detect header row and ID column; otherwise default to first column
    try:
        header = ws.row_values(1)
    except Exception:
        header = []
    col_index = None
    for i, h in enumerate(header):
        if h.strip().lower() == id_header.strip().lower():
            col_index = i + 1
            break
    if col_index is None:
        col_index = 1
    col_vals = ws.col_values(col_index)
    # drop header if present
    if col_vals and header and col_vals[0].strip().lower() == id_header.strip().lower():
        col_vals = col_vals[1:]
    existing = set()
    for v in col_vals:
        n = normalize_openalex_id(v)
        if n:
            existing.add(n)
    return existing

def append_rows_with_retries(ws, rows: List[List[str]], retries=APPEND_RETRIES):
    attempt = 0
    while True:
        try:
            ws.append_rows(rows, value_input_option="USER_ENTERED")
            return
        except Exception as e:
            attempt += 1
            if attempt > retries:
                raise
            backoff = min(INITIAL_BACKOFF * (2 ** (attempt - 1)) * (1 + random.random() * 0.1), MAX_BACKOFF)
            print(f"append_rows error (attempt {attempt}/{retries}): {e}. Sleeping {backoff:.1f}s then retrying.")
            time.sleep(backoff)

# --- Main incremental flow ---
def incremental_run_sheet_only():
    sheet_id = os.environ.get(SHEET_ID_ENV)
    if not sheet_id:
        raise ValueError("SHEET_ID environment variable not set.")
    client = gs_client_from_env()
    ws = open_worksheet(client, sheet_id, WORKSHEET_NAME)
    print("Opened worksheet:", ws.title)

    # 1) read existing ids
    existing_ids = read_existing_ids_from_sheet(ws)
    print(f"Existing IDs in sheet: {len(existing_ids)}")

    # 2) fetch remote papers via your pipeline fetcher
    fetched = fetch_openalex_for_journals()
    print(f"Fetched {len(fetched)} records from remote source.")

    # 3) normalize and index fetched by id
    fetched_by_id = {}
    for r in fetched:
        # account for possible keys
        raw = r.get("paperId") or r.get("paper_id") or r.get("id") or r.get("openalex_id")
        pid = normalize_openalex_id(raw)
        if not pid:
            continue
        fetched_by_id[pid] = r

    print(f"Normalized fetched ids: {len(fetched_by_id)}")

    # 4) compute new ids
    new_ids = [pid for pid in fetched_by_id.keys() if pid not in existing_ids]
    print(f"New papers to process: {len(new_ids)}")
    if not new_ids:
        print("No new items found. Exiting.")
        return

    # 5) process new items and append in batches
    batch_rows = []
    processed = 0
    for pid in tqdm(new_ids, desc="Processing new papers"):
        meta = fetched_by_id[pid]
        try:
            out = process_paper_by_meta(meta)
        except Exception as e:
            print(f"Error processing {pid}: {e}")
            continue

        # enforce normalized paper_id
        out["paper_id"] = pid

        # generate ordered row for FIELDNAMES
        row = [str(out.get(col, "")) for col in FIELDNAMES]
        batch_rows.append(row)
        processed += 1

        if len(batch_rows) >= APPEND_BATCH_SIZE:
            append_rows_with_retries(ws, batch_rows)
            print(f"Appended {len(batch_rows)} rows.")
            batch_rows = []

    if batch_rows:
        append_rows_with_retries(ws, batch_rows)
        print(f"Appended final {len(batch_rows)} rows.")

    print(f"Done. Processed and appended {processed} new papers.")

if __name__ == "__main__":
    start = time.time()
    incremental_run_sheet_only()
    print("Elapsed:", time.time() - start)