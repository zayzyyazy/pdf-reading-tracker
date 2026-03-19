# Document Reading Tracker

An AI-powered document processor that extracts text from PDF, TXT, and DOCX files, sends it to OpenAI, and saves structured metadata — title, summary, tags, category, and document type — into clean CSV files.

Built in Python as a personal knowledge management tool and my first serious coding project.

---

## Why I Built This

I read a lot — research papers, lecture notes, articles — and I kept losing track of what I had read and what it was about. I wanted a way to process a document and instantly get a summary and tags without doing it manually.

This project started as an experiment with the OpenAI API and grew into a full pipeline: drop a file in, get structured metadata out, organized by category.

---

## Features

- Accepts `.pdf`, `.txt`, and `.docx` files
- Extracts text from each document automatically
- Sends text to OpenAI (GPT-4o-mini) and receives back:
  - A short title
  - A 1–2 sentence summary
  - 2–3 descriptive tags
  - A category (e.g. `university`, `ai`, `philosophy`, `society`)
  - A document type (e.g. `research-paper`, `lecture-notes`, `article`)
- Saves results to a master CSV, per-category CSVs, and a category index
- Skips already-processed files to prevent duplicates
- Moves processed files to a `processed/` folder automatically
- Supports drag-and-drop on macOS via a shell script

---

## Tech Stack

| Layer | Tool |
|---|---|
| Language | Python 3.8+ |
| AI | OpenAI API — GPT-4o-mini |
| PDF extraction | pypdf |
| DOCX extraction | python-docx |
| Storage | CSV (standard library) |
| macOS entry point | Shell script (`run_drag.sh`) |

---

## How It Works

```
Drop file(s) into input/
        |
        v
app/main.py — finds files, checks for duplicates
        |
        v
app/pdf_reader.py — extracts raw text (PDF / TXT / DOCX)
        |
        v
app/ai_client.py — sends text to OpenAI, parses JSON response
        |
        v
app/storage.py — writes to:
    output/reads.csv            (master record)
    output/by-category/*.csv    (one file per category)
    output/index.csv            (category summary)
        |
        v
File moves to processed/
```

The AI prompt is structured to return strict JSON every time. The response is validated before anything is written to disk.

---

## Folder Structure

```
pdf-reading-tracker/
├── app/
│   ├── ai_client.py        # OpenAI API call and response parsing
│   ├── config.py           # Loads environment variables
│   ├── main.py             # Main orchestration loop
│   ├── pdf_reader.py       # Text extraction for PDF, TXT, DOCX
│   └── storage.py          # CSV writing and index management
├── docs/
│   ├── how-to-use.md       # Step-by-step usage guide
│   └── project-overview.md # Technical deep-dive
├── input/                  # Drop files here before running
├── processed/              # Files move here after processing
├── output/                 # Generated CSVs (git-ignored)
│   ├── reads.csv           # Master record of all documents
│   ├── index.csv           # Count and timestamp per category
│   └── by-category/        # One CSV per category
├── run_drag.sh             # macOS drag-and-drop launcher
├── requirements.txt
└── README.md
```

---

## How to Run

### 1. Clone the repo

```bash
git clone https://github.com/zayzyyazy/pdf-reading-tracker.git
cd pdf-reading-tracker
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Set your OpenAI API key

```bash
export OPENAI_API_KEY="your-key-here"
```

To make this permanent, add it to your shell profile (`~/.zshrc` or `~/.bash_profile`).

### 4. Add files and run

**Option A — process the input folder:**

```bash
# Drop files into input/, then:
python3 -m app.main
```

**Option B — pass files directly:**

```bash
python3 -m app.main path/to/file.pdf path/to/notes.txt
```

**Option C — drag and drop on macOS:**

Make the script executable once:

```bash
chmod +x run_drag.sh
```

Then configure `run_drag.sh` as a Folder Action or Automator drop target. Dragging files onto it copies them into `input/` and runs the processor automatically.

---

## Example Output

**Terminal:**

```
Document Reading Tracker starting...
Processing: machine-learning-intro.pdf
  Title:    Introduction to Machine Learning
  Category: ai
  Type:     article
  Tags:     machine learning, supervised learning, classification
  Summary:  A beginner-friendly overview of core ML concepts, covering supervised and unsupervised learning with practical examples.
  Saved to output/reads.csv
  Moved to processed/machine-learning-intro.pdf

Processing: descartes-essay.txt
  Title:    Descartes and the Problem of Knowledge
  Category: philosophy
  Type:     essay
  Tags:     epistemology, descartes, doubt
  Summary:  Explores Descartes' method of radical doubt and its implications for the foundations of human knowledge.
  Saved to output/reads.csv
  Moved to processed/descartes-essay.txt
```

**output/reads.csv:**

| processed_at | file_name | title | summary | tags | category | document_type |
|---|---|---|---|---|---|---|
| 2026-03-19T14:00:00 | machine-learning-intro.pdf | Introduction to Machine Learning | A beginner-friendly overview... | machine learning, supervised learning, classification | ai | article |
| 2026-03-19T14:01:00 | descartes-essay.txt | Descartes and the Problem of Knowledge | Explores Descartes' method... | epistemology, descartes, doubt | philosophy | essay |

**output/index.csv:**

| category | file_path | document_count | latest_processed_at |
|---|---|---|---|
| ai | output/by-category/ai.csv | 1 | 2026-03-19T14:00:00 |
| philosophy | output/by-category/philosophy.csv | 1 | 2026-03-19T14:01:00 |

---

## What I Learned

- How to structure a Python project into focused, single-responsibility modules
- How to call the OpenAI API and reliably parse structured JSON responses
- How to extract text from different file formats (PDF pages, DOCX paragraphs, plain text)
- How to build simple deduplication using a CSV as a lightweight record store
- How to use a shell script as a bridge between a macOS drag-and-drop workflow and a Python program
- Why environment variables matter for keeping secrets out of source code

This was my first time building something end-to-end that actually does something useful for me every day.

---

## Future Improvements

- [ ] A simple web UI so the tool can be used without a terminal
- [ ] Support for more file types (EPUB, Markdown, HTML)
- [ ] Export to Notion or Google Sheets
- [ ] Smarter deduplication by content hash rather than filename
- [ ] A dashboard to visualize reading trends over time
- [ ] Configurable category and document-type lists

---

*First serious Python project. Built to solve a real personal problem.*
