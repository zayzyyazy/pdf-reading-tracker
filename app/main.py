from datetime import datetime
from app.pdf_reader import extract_text_from_pdf
from app.ai_client import summarize_text
from app.storage import save_record

print("PDF Reading Tracker starting...")

text = extract_text_from_pdf("input/test.pdf")

result = summarize_text(text)

if result:
    print("\n--- AI Summary ---")
    print(f"Title:         {result['title']}")
    print(f"Primary Topic: {result['primary_topic']}")
    print(f"Tags:          {', '.join(result['tags'])}")
    print(f"Summary:       {result['summary']}")

    record = {
        "doc_id": datetime.now().strftime("%Y%m%d%H%M%S"),
        "file_name": "test.pdf",
        "source_path": "input/test.pdf",
        "processed_at": datetime.now().isoformat(),
        "title": result["title"],
        "summary": result["summary"],
        "summary_length": len(result["summary"]),
        "tags": ", ".join(result["tags"]),
        "primary_topic": result["primary_topic"],
        "text_length": len(text),
        "status": "processed",
        "error_message": "",
    }

    save_record(record)
    print("\nRow saved to output/reads.csv")
