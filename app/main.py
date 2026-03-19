from app.pdf_reader import extract_text_from_pdf

print("PDF Reading Tracker starting...")

text = extract_text_from_pdf("input/test.pdf")
print(text[:500])
