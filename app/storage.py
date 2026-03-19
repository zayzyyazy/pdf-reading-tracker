import csv
import os
from datetime import datetime

OUTPUT_DIR = "output"
CSV_FILE = os.path.join(OUTPUT_DIR, "reads.csv")
CATEGORY_DIR = os.path.join(OUTPUT_DIR, "by-category")
INDEX_FILE = os.path.join(OUTPUT_DIR, "index.csv")
INDEX_COLUMNS = ["category", "file_path", "document_count", "latest_processed_at"]

COLUMNS = [
    "processed_at",
    "file_name",
    "title",
    "summary",
    "tags",
    "category",
    "document_type",
]


def get_existing_file_names():
    if not os.path.isfile(CSV_FILE):
        return set()
    with open(CSV_FILE, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return {row["file_name"] for row in reader}


def save_record(record):
    # Create output folder if it doesn't exist
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Check if file already exists (to decide whether to write header)
    file_exists = os.path.isfile(CSV_FILE)

    with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)

        if not file_exists:
            writer.writeheader()

        writer.writerow(record)

    # Save to category-specific CSV
    category = record.get("category") or "other"
    if not category.strip():
        category = "other"

    os.makedirs(CATEGORY_DIR, exist_ok=True)
    category_file = os.path.join(CATEGORY_DIR, f"{category}.csv")
    category_file_exists = os.path.isfile(category_file)

    with open(category_file, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)

        if not category_file_exists:
            writer.writeheader()

        writer.writerow(record)

    # Update index.csv
    _update_index(category, record.get("processed_at", ""))


def _update_index(category, processed_at):
    # Build the path for this category's CSV file
    category_csv_path = os.path.join(CATEGORY_DIR, f"{category}.csv")

    # Count data rows in the category CSV (excluding the header)
    document_count = 0
    if os.path.isfile(category_csv_path):
        with open(category_csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            document_count = sum(1 for _ in reader)

    # Read existing index rows (if the file already exists)
    rows = []
    if os.path.isfile(INDEX_FILE):
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

    # Update the existing row for this category, or add a new one
    updated = False
    for row in rows:
        if row["category"] == category:
            row["document_count"] = document_count
            row["latest_processed_at"] = processed_at
            updated = True
            break

    if not updated:
        rows.append({
            "category": category,
            "file_path": category_csv_path,
            "document_count": document_count,
            "latest_processed_at": processed_at,
        })

    # Write the full index back (overwrite so counts stay accurate)
    # This also creates the file with a header if it did not exist yet
    with open(INDEX_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=INDEX_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
