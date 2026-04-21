# How to Use the Document Reading Tracker

This guide walks through everything from first-time setup to daily use.

---

## Prerequisites

- Python 3.8 or higher
- An OpenAI API key ([platform.openai.com](https://platform.openai.com))
- A terminal (macOS Terminal, iTerm2, or VS Code terminal)

---

## First-Time Setup

### 1. Install dependencies

From the project root:

```bash
pip install -r requirements.txt
```

This installs:
- `openai` — for talking to GPT-4o-mini
- `pypdf` — for extracting text from PDFs
- `python-docx` — for extracting text from DOCX files

### 2. Set your API key

The app reads your OpenAI key from an environment variable. The simplest way:

```bash
export OPENAI_API_KEY="sk-your-key-here"
```

To avoid re-typing this every session, add it to your shell profile (`~/.zshrc` or `~/.bash_profile`):

```bash
echo 'export OPENAI_API_KEY="sk-your-key-here"' >> ~/.zshrc
source ~/.zshrc
```

---

## Running the App

### Method 1 — Process the input folder

Drop your files into the `input/` folder, then run:

```bash
python3 -m app.main
```

The script scans `input/` for `.pdf`, `.txt`, and `.docx` files and processes each one.

### Method 2 — Pass files directly

You can point the script at any file(s) on your system without moving them first:

```bash
python3 -m app.main ~/Downloads/paper.pdf ~/Desktop/notes.txt
```

### Method 3 — Drag and drop on macOS

`run_drag.sh` is a shell script you can configure as a macOS drop target (e.g. via Folder Actions or Automator). When you drag files onto it, it copies them into `input/` and runs the processor automatically.

To make the script executable (one-time setup):

```bash
chmod +x run_drag.sh
```

---

## What Happens During Processing

For each file, the app:

1. Checks if the filename already exists in `output/reads.csv` — if yes, it skips it
2. Extracts the full text from the document
3. Sends the first ~3000 characters to GPT-4o-mini with a structured prompt
4. Parses the JSON response (title, summary, tags, category, document type)
5. Appends a row to `output/reads.csv`
6. Appends a row to `output/by-category/{category}.csv`
7. Updates `output/index.csv` with the latest count for that category
8. Moves the file to the `processed/` folder

---

## Reading Your Output

After processing, three CSV files are updated:

### `output/reads.csv` — the master log

Every document you have ever processed, in one file. Columns:

| Column | Description |
|---|---|
| `processed_at` | ISO timestamp of when it was processed |
| `file_name` | Original filename |
| `title` | AI-generated title |
| `summary` | 1-2 sentence summary |
| `tags` | 2-3 comma-separated lowercase tags |
| `category` | One of: `university`, `ai`, `society`, `politics`, `software`, `philosophy`, `personal`, `other` |
| `document_type` | One of: `article`, `essay`, `lecture-notes`, `tutorial`, `research-paper`, `other` |

Open it in Excel, Numbers, or any spreadsheet app to sort, filter, and search.

### `output/by-category/` — per-category files

Same columns as `reads.csv`, but split into separate files:
- `ai.csv`, `university.csv`, `philosophy.csv`, etc.

Useful when you only want to browse one topic.

### `output/index.csv` — the category overview

A quick summary table:

| Column | Description |
|---|---|
| `category` | Category name |
| `file_path` | Path to that category's CSV |
| `document_count` | How many documents are in that category |
| `latest_processed_at` | Timestamp of the most recently added document |

---

## Supported File Types

| Extension | How text is extracted |
|---|---|
| `.pdf` | Page-by-page text extraction via `pypdf` |
| `.txt` | Read directly as UTF-8 text |
| `.docx` | Paragraph-by-paragraph via `python-docx` |

Note: PDFs that are scanned images (not text-based) will not extract well. The app will process them but the AI output may be poor.

---

## Deduplication

The app tracks processed files by filename. If you run the script twice with the same file in `input/`, it will skip it the second time and print:

```
Skipping duplicate: my-paper.pdf
```

This means it is safe to run the script repeatedly without creating duplicate rows.

If you want to re-process a file (e.g. after editing the prompt), remove its row from `output/reads.csv` first.

---

## Tips

- The AI only reads the first ~3000 characters of each document. For long documents, make sure the most relevant content is near the top.
- Category and document type are constrained to fixed lists, so results are consistent and easy to filter.
- If a file fails to process, the app prints the error and continues — it will not crash the whole run.
- The `processed/` folder keeps your `input/` folder clean. Files there will not be re-processed.
