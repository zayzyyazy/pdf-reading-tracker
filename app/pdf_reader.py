from pypdf import PdfReader
from docx import Document


def _is_boilerplate_line(line: str) -> bool:
    low = line.lower()
    markers = (
        "springer nature",
        "terms of use",
        "license",
        "copyright",
        "all rights reserved",
        "http://",
        "https://",
        "doi:",
        "permissions",
        "reprints and permissions",
        "publisher",
        "author accepted manuscript",
    )
    if any(m in low for m in markers):
        return True
    # Drop heavily symbolic extraction junk.
    letters = sum(ch.isalpha() for ch in line)
    if letters < 8 and len(line) > 24:
        return True
    return False


def _clean_extracted_text(raw: str) -> str:
    lines = []
    for ln in (raw or "").splitlines():
        s = " ".join(ln.strip().split())
        if not s:
            continue
        if _is_boilerplate_line(s):
            continue
        lines.append(s)
    return "\n".join(lines).strip()


def extract_text_from_pdf(path):
    reader = PdfReader(path)
    pages = []
    line_counts: dict[str, int] = {}
    for page in reader.pages:
        try:
            extracted = page.extract_text() or ""
        except Exception:
            extracted = ""
        if extracted.strip():
            page_lines = [" ".join(ln.strip().split()) for ln in extracted.splitlines() if ln.strip()]
            pages.append(page_lines)
            seen = set(page_lines)
            for ln in seen:
                line_counts[ln] = line_counts.get(ln, 0) + 1
    if not pages:
        return ""
    repeat_threshold = max(2, int(len(pages) * 0.6))
    kept = []
    for page_lines in pages:
        for ln in page_lines:
            if line_counts.get(ln, 0) >= repeat_threshold and len(ln) < 120:
                # Likely repeated header/footer or legal strip.
                continue
            kept.append(ln)
    joined = "\n".join(kept)
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
