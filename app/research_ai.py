"""
Optional OpenAI-backed helpers for the research workspace.
Falls back to simple heuristics when no API key is configured or the API is unavailable.
"""
from __future__ import annotations

import hashlib
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


def build_deep_dive(
    subtopic: dict[str, Any],
    resources: list[dict[str, Any]],
    questions: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Build a structured deep dive grounded in one subtopic's local materials.
    Returns a payload with sections and a source digest.
    """
    resource_lines, question_lines, source_blob, source_digest = _deep_dive_source_snapshot(
        subtopic, resources, questions
    )

    client = _client()
    if not client:
        return _offline_deep_dive(subtopic, resource_lines, question_lines, source_digest)

    prompt = (
        "You are synthesizing a serious research topic deep dive for one subtopic.\n"
        "Ground every claim in the provided resources/questions.\n"
        "Avoid generic filler and avoid corporate prose.\n"
        "Return strict JSON with these keys only:\n"
        "{\n"
        '  "overview": "4-7 sentences",\n'
        '  "key_themes": ["..."],\n'
        '  "resource_connections": ["..."],\n'
        '  "tensions_and_gaps": ["..."],\n'
        '  "important_vocabulary": ["term: why it matters"],\n'
        '  "field_framing": "1-3 sentences",\n'
        '  "writing_angles": ["..."],\n'
        '  "next_questions": ["..."]\n'
        "}\n\n"
        "Rules:\n"
        "- Be specific to this subtopic.\n"
        "- Mention concrete resource titles where useful.\n"
        "- Keep each bullet concise and intellectually meaningful.\n"
        "- If evidence is thin, state uncertainty directly.\n\n"
        f"Subtopic name: {subtopic.get('name') or ''}\n"
        f"Research field: {subtopic.get('research_field') or ''}\n"
        f"Topic summary: {subtopic.get('topic_summary') or ''}\n\n"
        f"Resources and questions (JSON):\n{source_blob[:14000]}"
    )
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or "{}"
        data = json.loads(raw)
        return _normalize_deep_dive_payload(data, source_digest)
    except (json.JSONDecodeError, Exception):
        return _offline_deep_dive(subtopic, resource_lines, question_lines, source_digest)


def deep_dive_source_digest(
    subtopic: dict[str, Any],
    resources: list[dict[str, Any]],
    questions: list[dict[str, Any]],
) -> str:
    _, _, _, source_digest = _deep_dive_source_snapshot(subtopic, resources, questions)
    return source_digest


def _deep_dive_source_snapshot(
    subtopic: dict[str, Any],
    resources: list[dict[str, Any]],
    questions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str, str]:
    resource_lines = []
    for r in resources:
        title = (r.get("title") or "Untitled resource").strip()
        summary = (r.get("summary") or "").strip()
        notes = (r.get("notes") or "").strip()
        source_type = (r.get("source_type") or "other").strip()
        resource_lines.append(
            {
                "id": r.get("id"),
                "title": title,
                "source_type": source_type,
                "summary": summary,
                "notes": notes,
            }
        )

    question_lines = []
    for q in questions:
        body = (q.get("body") or "").strip()
        if body:
            question_lines.append({"id": q.get("id"), "body": body, "explored": bool(q.get("explored"))})

    source_blob = json.dumps(
        {
            "subtopic": {
                "id": subtopic.get("id"),
                "name": subtopic.get("name"),
                "research_field": subtopic.get("research_field"),
                "topic_summary": subtopic.get("topic_summary"),
            },
            "resources": resource_lines,
            "questions": question_lines,
        },
        ensure_ascii=True,
        sort_keys=True,
    )
    source_digest = hashlib.sha256(source_blob.encode("utf-8")).hexdigest()
    return resource_lines, question_lines, source_blob, source_digest


def _normalize_deep_dive_payload(data: dict[str, Any], source_digest: str) -> dict[str, Any]:
    def _to_list(v: Any, fallback: list[str]) -> list[str]:
        if not isinstance(v, list):
            return fallback
        out = [str(x).strip() for x in v if str(x).strip()]
        return out if out else fallback

    overview = str(data.get("overview") or "").strip() or "No overview available yet."
    field_framing = str(data.get("field_framing") or "").strip() or "Field framing is still emerging in this subtopic."
    payload = {
        "overview": overview,
        "key_themes": _to_list(data.get("key_themes"), ["Theme extraction needs more source detail."]),
        "resource_connections": _to_list(data.get("resource_connections"), ["Connections between resources remain under-specified."]),
        "tensions_and_gaps": _to_list(data.get("tensions_and_gaps"), ["Current materials leave major open tensions."]),
        "important_vocabulary": _to_list(data.get("important_vocabulary"), []),
        "field_framing": field_framing,
        "writing_angles": _to_list(data.get("writing_angles"), ["Compare competing interpretations from the current sources."]),
        "next_questions": _to_list(data.get("next_questions"), ["What evidence would most change your current view of this topic?"]),
        "source_digest": source_digest,
    }
    return payload


def _offline_deep_dive(
    subtopic: dict[str, Any],
    resources: list[dict[str, Any]],
    questions: list[dict[str, Any]],
    source_digest: str,
) -> dict[str, Any]:
    titles = [r["title"] for r in resources if r.get("title")]
    resource_count = len(resources)
    question_count = len(questions)
    top_summaries = [r.get("summary", "") for r in resources if r.get("summary")]
    overview_bits = []
    if subtopic.get("topic_summary"):
        overview_bits.append(str(subtopic.get("topic_summary")).strip())
    if titles:
        overview_bits.append(
            f"This subtopic currently draws on {resource_count} resources, including {', '.join(titles[:3])}."
        )
    else:
        overview_bits.append("This subtopic has no attached resources yet, so synthesis quality is currently limited.")
    if question_count:
        overview_bits.append(f"It also includes {question_count} research questions that indicate open inquiry paths.")
    overview = " ".join([x for x in overview_bits if x]).strip()

    key_themes = []
    for s in top_summaries[:3]:
        text = s.replace("\n", " ").strip()
        if text:
            key_themes.append(text[:220])
    if not key_themes:
        key_themes = ["Collect at least two substantive resource summaries to extract stronger themes."]

    connections = []
    if len(titles) >= 2:
        connections.append(f"Read {titles[0]} and {titles[1]} in dialogue: where they reinforce each other versus diverge.")
    if question_count and titles:
        connections.append("Use the existing subtopic questions as an indexing layer across the current resources.")
    if not connections:
        connections = ["Connection mapping is limited until more resources or questions are added."]

    tension = ["Evidence base is currently thin; treat conclusions as provisional."]
    if questions:
        tension.append("Several open questions are still unresolved and should drive the next round of reading.")

    vocab = []
    if subtopic.get("research_field"):
        vocab.append(f"{subtopic.get('research_field')}: likely disciplinary lens guiding interpretation.")

    writing_angles = [
        "Argue for which question in this subtopic is most decision-relevant and why.",
        "Write a position piece comparing the strongest and weakest assumptions across the resources.",
    ]
    next_questions = [q["body"] for q in questions[:5]] or [
        "What is the most important missing resource type in this subtopic (empirical, theoretical, or critique)?"
    ]

    return {
        "overview": overview or "No overview available yet.",
        "key_themes": key_themes,
        "resource_connections": connections,
        "tensions_and_gaps": tension,
        "important_vocabulary": vocab,
        "field_framing": "This deep dive is generated in local-first fallback mode; refine after adding richer source summaries.",
        "writing_angles": writing_angles,
        "next_questions": next_questions,
        "source_digest": source_digest,
    }
