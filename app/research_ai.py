"""
Optional OpenAI-backed helpers for the research workspace.
Falls back to simple heuristics when no API key is configured or the API is unavailable.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import app.settings_store as settings_store


def _client():
    key = settings_store.effective_openai_key()
    if not key:
        return None
    from openai import OpenAI

    return OpenAI(api_key=key)


def _offline_summary(snippet: str) -> dict[str, str]:
    title = "Untitled source"
    lines = [ln.strip() for ln in snippet.splitlines() if ln.strip()]
    if lines:
        title = lines[0][:120]
    summary = (snippet[:500] + "…") if len(snippet) > 500 else snippet
    return {"title": title, "summary": summary.strip() or "No text extracted."}


def _fallback_questions(title: str) -> list[str]:
    t = title or "this material"
    return [
        f"What is the core claim in “{t[:80]}”, and what would falsify it?",
        "Which assumptions are implicit but load-bearing?",
        "How does this connect to work I already trust or distrust?",
        "What is the smallest experiment or reading that would change my mind?",
    ]


def _offline_subtopic_suggestions(category_name: str) -> list[str]:
    return [
        f"Core threads in {category_name or 'this area'}",
        "Methods and evidence",
        "Objections and edge cases",
    ]


def summarize_for_resource(text: str, max_chars: int = 4000) -> dict[str, str]:
    """Return title + summary from raw extracted text."""
    snippet = (text or "")[:max_chars]
    client = _client()
    if not client:
        return _offline_summary(snippet)

    prompt = (
        "Read the following document excerpt. Return JSON only with keys:\n"
        '- title: concise title\n'
        '- summary: 2-4 sentences in the reader\'s voice, not marketing copy\n'
        f"\n---\n{snippet}\n---\n"
    )
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or "{}"
        data = json.loads(raw)
        return {
            "title": (data.get("title") or "Untitled source").strip(),
            "summary": (data.get("summary") or "").strip(),
        }
    except (json.JSONDecodeError, Exception):
        return _offline_summary(snippet)


def generate_questions(
    title: str,
    summary: str,
    extra_context: str = "",
) -> list[str]:
    """Produce a short list of substantive follow-up questions."""
    title = title or "Untitled"
    summary = summary or ""
    ctx = (extra_context or "")[:2500]
    client = _client()
    if not client:
        return _fallback_questions(title)

    prompt = (
        "You help a careful reader think deeper.\n"
        "Given a source title, summary, and optional excerpt, propose 4-6 concrete questions.\n"
        "Rules:\n"
        "- Short lines, no numbering prefix in the string (the app will number them).\n"
        "- No generic study questions; tie to this specific content when possible.\n"
        "- Return JSON only: {\"questions\": [\"...\", \"...\"]}\n\n"
        f"title: {title}\n"
        f"summary: {summary}\n"
        f"excerpt:\n{ctx}\n"
    )
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or "{}"
        data = json.loads(raw)
        qs = data.get("questions") or []
        out = [str(q).strip() for q in qs if str(q).strip()]
        if out:
            return out[:8]
    except (json.JSONDecodeError, Exception):
        pass
    return _fallback_questions(title)


def refine_writing(
    draft: str,
    focus: str = "clarity, structure, and precision while preserving the author's voice",
) -> str:
    """Return a refined version; caller decides whether to apply."""
    draft = draft or ""

    def _local_cleanup(s: str) -> str:
        lines = [ln.rstrip() for ln in s.splitlines()]
        return "\n".join(lines).strip()

    client = _client()
    if not client:
        return _local_cleanup(draft)

    prompt = (
        "You are an editor helping the author refine their own notes.\n"
        "Do not change their stance or invent new claims.\n"
        f"Editing focus: {focus}\n"
        "Return only the revised text, no preamble.\n\n"
        f"---\n{draft[:12000]}\n---\n"
    )
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
        )
        text = (resp.choices[0].message.content or "").strip()
        return text or draft
    except Exception:
        return _local_cleanup(draft)


def suggest_subtopic_names(category_name: str, resource_titles: list[str]) -> list[str]:
    """Suggest a few subtopic cluster names (optional helper)."""
    client = _client()
    blob = "; ".join(resource_titles[:12])
    if not client:
        return _offline_subtopic_suggestions(category_name)

    prompt = (
        "Given a research category and a list of resource titles, propose 4-6 subtopic cluster names.\n"
        "Names should feel like shelves in a personal library, not course modules.\n"
        f"category: {category_name}\n"
        f"titles: {blob}\n"
        'Return JSON: {"subtopics": ["...", "..."]}\n'
    )
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or "{}"
        data = json.loads(raw)
        xs = data.get("subtopics") or []
        out = [str(x).strip() for x in xs if str(x).strip()][:8]
        return out if out else _offline_subtopic_suggestions(category_name)
    except (json.JSONDecodeError, Exception):
        return _offline_subtopic_suggestions(category_name)


def suggest_subtopic_for_resource(
    text_snippet: str,
    filename: str,
    subtopics: list[dict[str, Any]],
) -> Optional[str]:
    """
    Pick the best subtopic_id from the given list using the model, or None on failure.
    Each subtopic dict needs: id, name, category_name (optional research_field).
    """
    if not subtopics:
        return None
    snippet = (text_snippet or "")[:3500]
    client = _client()
    lines = []
    for s in subtopics:
        cid = s.get("id", "")
        nm = s.get("name", "")
        cat = s.get("category_name", "")
        rf = s.get("research_field") or ""
        extra = f" [{rf}]" if rf else ""
        lines.append(f"- id: {cid} | category: {cat} | subtopic: {nm}{extra}")
    catalog = "\n".join(lines)

    if not client:
        return subtopics[0]["id"]

    prompt = (
        "You help file a research source into the best existing shelf.\n"
        "Choose exactly one subtopic id from the list. Prefer semantic fit over name similarity.\n"
        "Return JSON only: {\"subtopic_id\": \"<uuid>\", \"reason\": \"one short phrase\"}\n\n"
        f"filename: {filename}\n"
        f"excerpt:\n{snippet}\n\n"
        f"Allowed subtopics:\n{catalog}\n"
    )
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or "{}"
        data = json.loads(raw)
        sid = (data.get("subtopic_id") or "").strip()
        valid = {s["id"] for s in subtopics}
        if sid in valid:
            return sid
    except (json.JSONDecodeError, Exception, KeyError):
        pass
    return subtopics[0]["id"]


def infer_source_type(filename: str) -> str:
    ext = ""
    if "." in filename:
        ext = filename.rsplit(".", 1)[-1].lower()
    mapping = {
        "pdf": "pdf",
        "txt": "notes",
        "docx": "article",
        "md": "notes",
    }
    return mapping.get(ext, "other")
