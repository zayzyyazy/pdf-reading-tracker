import os
import glob
from datetime import datetime

from app.storage import save_record
from app.pdf_reader import extract_text_from_pdf

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RAW_DIR  = os.path.join(BASE_DIR, "data", "raw")


def process_pdf(pdf_path):
    filename = os.path.basename(pdf_path)
    print(f"\n── {filename}")

    # 1. Extract text
    print("   Extracting text...")
    text = extract_text_from_pdf(pdf_path)

    if not text:
        print("   WARNING: no text extracted. Skipping.")
        return

    # 2. Fake minimal analysis (we’ll improve later)
    print("   Creating record...")

    record = {
        "processed_at": datetime.now().isoformat(),
        "file_name": filename,
        "title": filename.replace(".pdf", ""),
        "summary": text[:300],  # just preview for now
        "tags": "",
        "category": "general",
        "document_type": "pdf",
    }

    # 3. Save
    save_record(record)
    print("   Saved to CSV")


def main():
    pdfs = glob.glob(os.path.join(RAW_DIR, "*.pdf"))

    if not pdfs:
        print(f"No PDFs found in {RAW_DIR}")
        return

    print(f"Found {len(pdfs)} PDF(s)")

    for pdf_path in pdfs:
        try:
            process_pdf(pdf_path)
        except Exception as e:
            print(f"ERROR: {e}")

    print("\nDone.")


if __name__ == "__main__":
    main()
