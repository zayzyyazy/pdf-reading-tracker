import os
import sys
import glob
import shutil
from datetime import datetime
from app.pdf_reader import extract_text_from_pdf, extract_text_from_txt, extract_text_from_docx
from app.ai_client import summarize_text
from app.storage import save_record, get_existing_file_names
from app.config import INPUT_FOLDER

SUPPORTED_EXTENSIONS = (".pdf", ".txt", ".docx")
PROCESSED_FOLDER = "processed"

def move_to_processed(file_path):
    """Move a file into the processed/ folder. Adds a suffix if name already exists."""
    os.makedirs(PROCESSED_FOLDER, exist_ok=True)
    file_name = os.path.basename(file_path)
    dest = os.path.join(PROCESSED_FOLDER, file_name)

    # If a file with the same name exists, add a numbered suffix
    if os.path.exists(dest):
        base, ext = os.path.splitext(file_name)
        counter = 1
        while os.path.exists(dest):
            dest = os.path.join(PROCESSED_FOLDER, f"{base}-{counter}{ext}")
            counter += 1

    try:
        shutil.move(file_path, dest)
        print(f"  Moved to {dest}")
    except Exception as e:
        print(f"  Warning: could not move file — {e}")

# Use file paths from command-line arguments, or fall back to the input folder
if len(sys.argv) > 1:
    all_files = [p for p in sys.argv[1:] if p.endswith(SUPPORTED_EXTENSIONS)]
    print(f"PDF Reading Tracker starting...")
    print(f"Processing {len(all_files)} file(s) from command line\n")
else:
    input_folder = INPUT_FOLDER or "input"
    all_files = []
    for ext in SUPPORTED_EXTENSIONS:
        all_files.extend(glob.glob(os.path.join(input_folder, f"*{ext}")))
    print(f"PDF Reading Tracker starting...")
    print(f"Input folder: {input_folder}")
    print(f"Found {len(all_files)} file(s)\n")

existing_file_names = get_existing_file_names()

for file_path in all_files:
    file_name = os.path.basename(file_path)

    if file_name in existing_file_names:
        print(f"Skipping duplicate: {file_name}")
        continue

    ext = os.path.splitext(file_name)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        print(f"Skipping unsupported file type: {file_name}\n")
        continue

    print(f"Processing: {file_name}")

    try:
        if ext == ".pdf":
            text = extract_text_from_pdf(file_path)
        elif ext == ".txt":
            text = extract_text_from_txt(file_path)
        elif ext == ".docx":
            text = extract_text_from_docx(file_path)

        result = summarize_text(text)

        if result:
            print(f"  Title:    {result['title']}")
            print(f"  Category: {result['category']}")
            print(f"  Type:     {result['document_type']}")
            print(f"  Tags:     {', '.join(result['tags'])}")
            print(f"  Summary:  {result['summary']}")

            record = {
                "processed_at": datetime.now().isoformat(),
                "file_name": file_name,
                "title": result["title"],
                "summary": result["summary"],
                "tags": ", ".join(result["tags"]),
                "category": result["category"],
                "document_type": result["document_type"],
            }

            save_record(record)
            print(f"  Saved to output/reads.csv")
            move_to_processed(file_path)
            print()
        else:
            print(f"  Skipped: summarize returned no result\n")

    except Exception as e:
        print(f"  Failed: {e} — skipping\n")
        continue
