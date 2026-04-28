import re

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
        "disclaim and waive",
        "implied warranties",
        "non-commercial use",
        "small scale, personal",
        "re-use permitted",
        "competing interests",
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
        s = _normalize_line(ln)
        if not s:
            continue
        if _is_boilerplate_line(s):
            continue
        lines.append(s)
    merged = _recover_paragraphs(lines)
    return "\n".join(merged).strip()


def _normalize_line(line: str) -> str:
    s = (line or "").replace("\u00ad", "")
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(
        r"([a-z\)])\s+(The findings|These findings|This study|The study|Results|Discussion|Conclusion)\b",
        r"\1. \2",
        s,
    )
    if not s:
        return ""
    # Drop pure page numbers and citation index noise.
    if re.fullmatch(r"\[?\d{1,3}\]?", s):
        return ""
    if re.fullmatch(r"page \d+ of \d+", s.lower()):
        return ""
    return s


def _looks_like_heading(line: str) -> bool:
    low = line.lower().strip().strip(":")
    if low in {
        "abstract",
        "introduction",
        "background",
        "methods",
        "method",
        "results",
        "findings",
        "discussion",
        "conclusion",
        "references",
    }:
        return True
    if len(line.split()) <= 8 and line.isupper():
        return True
    if re.match(r"^\d+(\.\d+)*\s+[A-Za-z]", line):
        return True
    return False


def _recover_paragraphs(lines: list[str]) -> list[str]:
    if not lines:
        return []
    paragraphs: list[str] = []
    current = ""
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if _looks_like_heading(line):
            if current:
                paragraphs.append(current.strip())
                current = ""
            paragraphs.append(line)
            continue
        if not current:
            current = line
            continue
        # De-hyphenate line wraps from PDF extraction.
        if current.endswith("-") and line and line[0].islower():
            current = current[:-1] + line
            continue
        # Join broken PDF lines. Do not split on every capitalized line — that
        # fragments news-style and two-column extraction into junk "paragraphs".
        prev_end = current[-1]
        if prev_end in ".!?":
            paragraphs.append(current.strip())
            current = line
        elif prev_end in ":;" and len(current) > 120:
            paragraphs.append(current.strip())
            current = line
        else:
            current = f"{current} {line}"
    if current:
        paragraphs.append(current.strip())
    # Filter low-information and repeated paragraph noise.
    out: list[str] = []
    seen: set[str] = set()
    for p in paragraphs:
        if len(p) < 30 and not _looks_like_heading(p):
            continue
        key = " ".join(p.lower().split()[:12])
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


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
            page_lines = [_normalize_line(ln) for ln in extracted.splitlines()]
            page_lines = [ln for ln in page_lines if ln]
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
