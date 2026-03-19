import csv
import os
from datetime import datetime

OUTPUT_DIR = "output"
CSV_FILE = os.path.join(OUTPUT_DIR, "reads.csv")

COLUMNS = [
    "doc_id",
    "file_name",
    "source_path",
    "processed_at",
    "title",
    "summary",
    "summary_length",
    "tags",
    "primary_topic",
    "text_length",
    "status",
    "error_message",
]


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
