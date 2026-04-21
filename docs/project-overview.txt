# Project Overview

A technical walkthrough of how the Document Reading Tracker is built.

---

## Purpose

This project is a personal knowledge management tool. It takes documents (PDF, TXT, DOCX) and automatically extracts meaning from them — generating a title, summary, tags, category, and document type — then stores everything in structured CSV files that are easy to browse and query.

The goal was to build something genuinely useful, not just a tutorial exercise.

---

## Architecture

The project is a simple, linear pipeline with four modules under `app/`:

```
main.py
  |__ pdf_reader.py   (text extraction)
  |__ ai_client.py    (AI summarization)
  |__ storage.py      (CSV output)
  |__ config.py       (environment variables)
```

There is no web server, no database, and no external services beyond the OpenAI API. Everything runs locally.

---

## Module Breakdown

### `app/config.py`

Loads three environment variables:

- `OPENAI_API_KEY` — required for the AI step
- `INPUT_FOLDER` — optional override for the input directory (defaults to `input/`)
- `GOOGLE_SHEETS_SPREADSHEET_ID` — leftover from an earlier planned feature, not currently used

### `app/pdf_reader.py`

Three functions, one per supported file type:

- `extract_text_from_pdf(path)` — uses `pypdf` to iterate through pages and concatenate text
- `extract_text_from_txt(path)` — reads the file directly with UTF-8 encoding
- `extract_text_from_docx(path)` — uses `python-docx` to iterate through paragraphs

All three return a plain string of extracted text.

### `app/ai_client.py`

The core AI step. Takes a string of text and returns structured metadata.

**What it sends to OpenAI:**

A prompt asking for a JSON object with five keys:
- `title` — short document title
- `summary` — 1-2 sentences, max ~220 characters
- `tags` — list of exactly 2-3 lowercase keywords
- `category` — one of eight fixed values
- `document_type` — one of six fixed values

The model is `gpt-4o-mini` with `response_format: json_object` enabled, which forces the model to return valid JSON rather than freeform text.

Only the first 3000 characters of the extracted text are sent. This keeps costs low and response times fast while still giving the model enough context for most documents.

**Response handling:**

The response is parsed with `json.loads()`. Each field is validated and falls back to a safe default if missing or malformed. Returns `None` if the JSON cannot be parsed at all.

**Available categories:**
`university`, `ai`, `society`, `politics`, `software`, `philosophy`, `personal`, `other`

**Available document types:**
`article`, `essay`, `lecture-notes`, `tutorial`, `research-paper`, `other`

### `app/storage.py`

Handles all CSV output. Three files are maintained:

**`output/reads.csv`** — the master record. One row per document, columns:
`processed_at`, `file_name`, `title`, `summary`, `tags`, `category`, `document_type`

The file is created with a header on first write, then appended to on subsequent writes. This avoids loading the full file into memory.

**`output/by-category/{category}.csv`** — the same row is also written to a category-specific file. So a document categorized as `ai` appears in both `reads.csv` and `by-category/ai.csv`.

**`output/index.csv`** — a summary table updated after each write. For each category, it stores the path to that category's CSV, the current document count, and the timestamp of the most recently added document.

The index is rewritten in full on each update (read, modify in memory, write back). This keeps it accurate even if rows are manually deleted from the category files.

**Deduplication:**

Before processing begins, `get_existing_file_names()` reads all `file_name` values from `reads.csv` into a Python `set`. The main loop checks each incoming file against this set and skips duplicates. This is fast and simple — it does not require loading or comparing file contents.

### `app/main.py`

The entry point. Handles two modes:

- **Command-line arguments:** `sys.argv[1:]` are treated as file paths and processed directly
- **Folder scan:** if no arguments are given, all supported files in `input/` are collected with `glob`

For each file, it:
1. Checks for duplicates
2. Calls the appropriate extraction function
3. Calls `summarize_text()`
4. Builds a record dict
5. Calls `save_record()`
6. Calls `move_to_processed()` — moves the file to `processed/`, adding a numeric suffix if a file with the same name already exists there

Errors on individual files are caught and logged, and the loop continues to the next file.

---

## Shell Script

`run_drag.sh` enables a drag-and-drop workflow on macOS. It:

1. Changes directory to the project root (using `$(dirname "$0")` so it works from anywhere)
2. Copies all dragged files into `input/`
3. Runs `python3 -m app.main`
4. Waits for a keypress before closing (so the terminal output stays visible)

---

## Design Decisions

**Why CSV instead of a database?**
CSV files are simple, portable, and readable in any spreadsheet application. For personal use at this scale, a database would add complexity without real benefit.

**Why fixed category and document type lists?**
Free-form AI output is inconsistent and hard to filter. Constraining the output to known values makes the CSVs immediately queryable. The prompt lists the allowed values explicitly, and the AI respects them reliably when `json_object` mode is enabled.

**Why only the first 3000 characters?**
Most documents front-load their key information (abstract, introduction, title). Sending 3000 characters keeps API costs near zero while still giving the model enough context. The tradeoff is that the summary may miss content from the body of long documents.

**Why move files to `processed/` instead of deleting them?**
Deleting files is irreversible. Moving them keeps originals accessible while keeping `input/` clean for the next run.

---

## Earlier Architecture

The first version of this project was planned around n8n (a workflow automation tool), Google Sheets as storage, and Power BI for visualization. That architecture was scrapped in favor of a simpler, fully self-contained Python script.
