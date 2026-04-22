from pypdf import PdfReader
from docx import Document


def _clean_extracted_text(raw: str) -> str:
    lines = []
    for ln in (raw or "").splitlines():
        s = " ".join(ln.strip().split())
        if not s:
            continue
        lines.append(s)
    return "\n".join(lines).strip()


def extract_text_from_pdf(path):
    reader = PdfReader(path)
    pages = []
    for page in reader.pages:
        try:
            extracted = page.extract_text() or ""
        except Exception:
            extracted = ""
        if extracted.strip():
            pages.append(extracted)
    joined = "\n\n".join(pages)
    return _clean_extracted_text(joined)


def extract_text_from_txt(path):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return _clean_extracted_text(f.read())


def extract_text_from_docx(path):
    doc = Document(path)
    text = []
    for paragraph in doc.paragraphs:
        text.append(paragraph.text)
    return _clean_extracted_text("\n".join(text))
