import re

from pypdf import PdfReader
from docx import Document

# Second half of a split token must not be merged into a common English word.
_MERGE_BLOCK_SECOND = frozenset(
    "the and for from with this that are was were can may not but has have "
    "models work study paper data use time like line case cases into onto "
    "will would could should their there these those when where which while "
    "frontiers states years times works forms types sites areas levels "
    "models users cases points lines means ways days".split()
)
_BAD_MERGE_FIRST = frozenset("new non pre pro anti sub mid the one two any all".split())


def _repair_hyphen_space_artifacts(text: str) -> str:
    """Fix 'AI- generated' / 'child -like' style hyphen+space PDF glitches."""
    s = (text or "").replace("\ufb01", "fi").replace("\ufb02", "fl")
    s = re.sub(r"-\s+([a-z])", r"-\1", s, flags=re.IGNORECASE)
    return s


def _repair_titlecase_word_splits(text: str) -> str:
    """Join 'Bey ond' / 'Tog ether' style breaks: TitleCase-prefix + lowercase word."""

    def repl(m: re.Match) -> str:
        a, b = m.group(1), m.group(2)
        if a.lower() in _BAD_MERGE_FIRST:
            return m.group(0)
        first_b = b.split()[0].lower() if b else ""
        if first_b in _MERGE_BLOCK_SECOND:
            return m.group(0)
        if len(a) + len(b) > 22:
            return m.group(0)
        if a[0].isupper() and a[1:].islower() and b and b[0].islower():
            return a + b
        return m.group(0)

    return re.sub(r"\b([A-Z][a-z]{1,3})\s([a-z]{3,})\b", repl, text)


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
        "creative commons",
        "licence for details",
        "license for details",
        "version of record",
        "author manuscript",
        "condition of access",
        "permitted re-use",
        "permitted reuse",
        "personal use and",
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
    fixed = [_repair_hyphen_space_artifacts(_repair_titlecase_word_splits(p)) for p in merged]
    return "\n".join(fixed).strip()


def _normalize_line(line: str) -> str:
    s = (line or "").replace("\u00ad", "")
    s = s.replace("\ufb01", "fi").replace("\ufb02", "fl")
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
