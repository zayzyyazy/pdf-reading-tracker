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
    points = _extract_signal_sentences(snippet, max_items=4)
    if points:
        summary = " ".join(points[:3])
    else:
        summary = (snippet[:500] + "…") if len(snippet) > 500 else snippet
    return {"title": title, "summary": summary.strip() or "No text extracted."}


def _fallback_questions(title: str, evidence_pack: str = "") -> list[str]:
    t = title or "this material"
    sig = _extract_signal_sentences(evidence_pack, max_items=2)
    anchor = sig[0] if sig else ""
    return [
        f"What is the core claim in “{t[:80]}”, and what evidence in the source best supports it?",
        "Which assumptions are implicit but load-bearing, and where are they visible in the text?",
        f"If the source says '{anchor[:90]}' what tension or counterexample should be tested?" if anchor else "Which argument step is least justified by the source's own evidence?",
        "What is the smallest follow-up reading or test that could materially change your view of this source?",
    ]


def _offline_subtopic_suggestions(category_name: str) -> list[str]:
    return [
        f"Core threads in {category_name or 'this area'}",
        "Methods and evidence",
        "Objections and edge cases",
    ]


def _clean_label(value: str, fallback: str, max_len: int = 64) -> str:
    txt = " ".join((value or "").strip().split())
    if not txt:
        txt = fallback
    if len(txt) > max_len:
        txt = txt[:max_len].rstrip(" -_,.;:")
    return txt


def _label_from_snippet(snippet: str, fallback: str = "New inquiry") -> str:
    stop = {
        "the",
        "and",
        "for",
        "with",
        "into",
        "from",
        "that",
        "this",
        "about",
        "through",
        "across",
        "between",
        "your",
        "their",
        "resource",
    }
    words = []
    for raw in (snippet or "").replace("\n", " ").split():
        token = "".join(ch for ch in raw.lower() if ch.isalpha())
        if len(token) < 4 or token in stop:
            continue
        words.append(token)
        if len(words) == 3:
            break
    if not words:
        return fallback
    return " ".join(w.capitalize() for w in words)


def summarize_for_resource(text: str, max_chars: int = 4000) -> dict[str, str]:
    """Return title + summary from raw extracted text."""
    snippet = _build_evidence_pack(text or "", max_chars=max_chars)
    client = _client()
    if not client:
        return _offline_summary(snippet)

    prompt = (
        "Read this evidence pack extracted from one source.\n"
        "Infer what the source is specifically about based on quoted content.\n"
        "Return JSON only with keys:\n"
        '- title: concise source title (6-14 words, source-specific)\n'
        '- summary: 4-7 sentences grounded in the evidence. Mention at least two concrete claims/ideas from the text.\n'
        "Rules:\n"
        "- Do not write generic academic filler.\n"
        "- If the text is partial/noisy, state uncertainty briefly instead of guessing.\n"
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
    ctx = _build_evidence_pack(extra_context or "", max_chars=9000)
    client = _client()
    if not client:
        return _fallback_questions(title, ctx)

    prompt = (
        "You help a careful reader think deeper.\n"
        "Given a source title, summary, and evidence pack from the source, propose 5-8 concrete questions.\n"
        "Rules:\n"
        "- Short lines, no numbering prefix in the string (the app will number them).\n"
        "- No generic study questions; tie each question to specific claims/terms from this source.\n"
        "- At least 2 questions should probe assumptions or methodological tension.\n"
        "- At least 2 questions should be useful as writing/opinion entry points.\n"
        "- Return JSON only: {\"questions\": [\"...\", \"...\"]}\n\n"
        f"title: {title}\n"
        f"summary: {summary}\n"
        f"evidence_pack:\n{ctx}\n"
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
    return _fallback_questions(title, ctx)


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


def decide_resource_placement(
    text_snippet: str,
    filename: str,
    categories: list[dict[str, Any]],
    subtopics: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Decide whether to place in existing subtopic, create subtopic, or create category+subtopic.
    """
    if not subtopics:
        cat_name = _clean_label(_label_from_snippet(text_snippet), "New research area", 44)
        return {
            "action": "new_category",
            "category_name": cat_name,
            "category_description": "Auto-created from intake because no existing category matched.",
            "subtopic_name": "Core questions",
            "research_field": "",
            "topic_summary": "",
            "reason": "No existing structure available.",
        }
    snippet = _build_evidence_pack(text_snippet or "", max_chars=10000)
    client = _client()
    cats = [{"id": c.get("id"), "name": c.get("name"), "description": c.get("description") or ""} for c in categories]
    subs = []
    by_cat = {c.get("id"): c.get("name") for c in categories}
    for s in subtopics:
        subs.append(
            {
                "id": s.get("id"),
                "name": s.get("name"),
                "category_id": s.get("category_id"),
                "category_name": s.get("category_name") or by_cat.get(s.get("category_id"), ""),
                "research_field": s.get("research_field") or "",
            }
        )
    if not client:
        return _offline_placement_decision(filename, snippet, cats, subs)

    prompt = (
        "You are routing one research resource into an evolving knowledge map.\n"
        "Choose ONE action: existing_subtopic, new_subtopic, new_category.\n"
        "Conservative rule: prefer existing_subtopic unless mismatch is clear.\n"
        "If a category fits but subtopic missing, choose new_subtopic.\n"
        "Only choose new_category when existing categories are meaningfully wrong.\n"
        "Naming rules: specific, concise, non-generic, not overlong, not awkward.\n"
        "Return JSON only with keys:\n"
        "{"
        '"action":"existing_subtopic|new_subtopic|new_category",'
        '"existing_subtopic_id":"",'
        '"existing_category_id":"",'
        '"category_name":"",'
        '"category_description":"",'
        '"subtopic_name":"",'
        '"research_field":"",'
        '"topic_summary":"",'
        '"reason":"one short sentence"'
        "}\n\n"
        f"filename: {filename}\n"
        f"text evidence pack:\n{snippet}\n\n"
        f"Categories JSON:\n{json.dumps(cats, ensure_ascii=True)}\n\n"
        f"Subtopics JSON:\n{json.dumps(subs, ensure_ascii=True)}\n"
    )
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or "{}"
        data = json.loads(raw)
        return _normalize_placement_decision(data, cats, subs, filename, snippet)
    except (json.JSONDecodeError, Exception):
        return _offline_placement_decision(filename, snippet, cats, subs)


def _normalize_placement_decision(
    data: dict[str, Any],
    categories: list[dict[str, Any]],
    subtopics: list[dict[str, Any]],
    filename: str,
    snippet: str,
) -> dict[str, Any]:
    valid_sub = {s["id"] for s in subtopics if s.get("id")}
    valid_cat = {c["id"] for c in categories if c.get("id")}
    action = str(data.get("action") or "").strip().lower()
    if action not in {"existing_subtopic", "new_subtopic", "new_category"}:
        return _offline_placement_decision(filename, snippet, categories, subtopics)
    result = {
        "action": action,
        "existing_subtopic_id": str(data.get("existing_subtopic_id") or "").strip(),
        "existing_category_id": str(data.get("existing_category_id") or "").strip(),
        "category_name": _clean_label(str(data.get("category_name") or ""), "New area", 52),
        "category_description": _clean_label(str(data.get("category_description") or ""), "", 180),
        "subtopic_name": _clean_label(str(data.get("subtopic_name") or ""), "Core threads", 56),
        "research_field": _clean_label(str(data.get("research_field") or ""), "", 60),
        "topic_summary": _clean_label(str(data.get("topic_summary") or ""), "", 220),
        "reason": _clean_label(str(data.get("reason") or ""), "Auto-routed by model.", 180),
    }
    if action == "existing_subtopic":
        if result["existing_subtopic_id"] not in valid_sub:
            return _offline_placement_decision(filename, snippet, categories, subtopics)
        return result
    if action == "new_subtopic":
        if result["existing_category_id"] not in valid_cat:
            return _offline_placement_decision(filename, snippet, categories, subtopics)
        return result
    return result


def _offline_placement_decision(
    filename: str,
    snippet: str,
    categories: list[dict[str, Any]],
    subtopics: list[dict[str, Any]],
) -> dict[str, Any]:
    lower = (snippet[:1400] or "").lower()
    best = None
    best_score = -1
    for s in subtopics:
        score = 0
        for token in (s.get("name") or "").lower().split():
            if len(token) > 3 and token in lower:
                score += 2
        for token in (s.get("category_name") or "").lower().split():
            if len(token) > 3 and token in lower:
                score += 1
        if score > best_score:
            best = s
            best_score = score
    if best and best_score >= 3:
        return {
            "action": "existing_subtopic",
            "existing_subtopic_id": best["id"],
            "reason": "Matched existing subtopic by keyword overlap.",
        }
    cat = None
    cat_score = -1
    for c in categories:
        score = 0
        for token in (c.get("name") or "").lower().split():
            if len(token) > 3 and token in lower:
                score += 1
        if score > cat_score:
            cat = c
            cat_score = score
    base = _clean_label(_label_from_snippet(snippet, filename.rsplit(".", 1)[0].replace("_", " ")), "New inquiry", 44)
    if cat and cat_score >= 1:
        return {
            "action": "new_subtopic",
            "existing_category_id": cat["id"],
            "subtopic_name": f"{base} perspectives",
            "research_field": "",
            "topic_summary": "Auto-created because this resource did not fit an existing subtopic cleanly.",
            "reason": "Category matched; subtopic appears missing.",
        }
    return {
        "action": "new_category",
        "category_name": f"{base} studies",
        "category_description": "Auto-created from intake because existing categories did not fit.",
        "subtopic_name": "Core questions",
        "research_field": "",
        "topic_summary": "Initial shelf generated from resource intake.",
        "reason": "No strong existing category match.",
    }


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
        f"Resources and questions (JSON):\n{source_blob[:22000]}"
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
    for s in top_summaries[:5]:
        key_themes.extend(_extract_signal_sentences(s, max_items=2))
    key_themes = [k[:220] for k in key_themes if k][:5]
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


def build_resource_deep_dive(
    resource: dict[str, Any],
    subtopic: dict[str, Any],
    questions: list[dict[str, Any]],
    excerpt: str = "",
) -> dict[str, Any]:
    source_digest = resource_deep_dive_source_digest(resource, questions)
    question_lines = [str(q.get("body") or "").strip() for q in questions if str(q.get("body") or "").strip()]
    evidence_pack = _build_evidence_pack(excerpt or "", max_chars=12000)
    client = _client()
    if not client:
        return _offline_resource_deep_dive(resource, subtopic, question_lines, evidence_pack, source_digest)
    prompt = (
        "Create a serious deep dive for a single research resource.\n"
        "Ground claims in the provided title/summary/notes/questions/evidence pack.\n"
        "Avoid generic prose.\n"
        "Return JSON with keys only:\n"
        "{"
        '"evidence_notes":["short quote or phrase from source + why it matters"],'
        '"resource_overview":"3-6 sentences",'
        '"strongest_ideas":["..."],'
        '"assumptions":["..."],'
        '"tensions_and_angles":["..."],'
        '"key_concepts":["term: why it matters"],'
        '"writing_angles":["..."],'
        '"next_questions":["..."]'
        "}\n\n"
        f"resource title: {resource.get('title') or ''}\n"
        f"source type: {resource.get('source_type') or ''}\n"
        f"subtopic: {subtopic.get('name') or ''}\n"
        f"summary: {resource.get('summary') or ''}\n"
        f"notes: {resource.get('notes') or ''}\n"
        f"questions: {json.dumps(question_lines[:12], ensure_ascii=True)}\n"
        f"evidence_pack:\n{evidence_pack}\n"
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
            "evidence_notes": _as_clean_list(data.get("evidence_notes"), []),
            "resource_overview": _clean_label(str(data.get("resource_overview") or ""), "No overview available yet.", 900),
            "strongest_ideas": _as_clean_list(data.get("strongest_ideas"), ["Identify the central claim and strongest support."]),
            "assumptions": _as_clean_list(data.get("assumptions"), ["What assumptions does this resource rely on?"]),
            "tensions_and_angles": _as_clean_list(data.get("tensions_and_angles"), ["Where does this resource conflict with alternatives?"]),
            "key_concepts": _as_clean_list(data.get("key_concepts"), []),
            "writing_angles": _as_clean_list(data.get("writing_angles"), ["Write a response testing this resource's strongest claim."]),
            "next_questions": _as_clean_list(data.get("next_questions"), question_lines[:4] or ["What would change your view of this resource?"]),
            "source_digest": source_digest,
        }
    except (json.JSONDecodeError, Exception):
        return _offline_resource_deep_dive(resource, subtopic, question_lines, evidence_pack, source_digest)


def _as_clean_list(value: Any, fallback: list[str]) -> list[str]:
    if not isinstance(value, list):
        return fallback
    out = [_clean_label(str(v), "", 220) for v in value]
    out = [x for x in out if x]
    return out if out else fallback


def resource_deep_dive_source_digest(resource: dict[str, Any], questions: list[dict[str, Any]]) -> str:
    source_blob = json.dumps(
        {
            "resource": {
                "id": resource.get("id"),
                "title": resource.get("title"),
                "source_type": resource.get("source_type"),
                "summary": resource.get("summary"),
                "notes": resource.get("notes"),
                "updated_at": resource.get("updated_at"),
            },
            "questions": [
                {"id": q.get("id"), "body": q.get("body"), "explored": bool(q.get("explored"))}
                for q in questions
            ],
        },
        ensure_ascii=True,
        sort_keys=True,
    )
    return hashlib.sha256(source_blob.encode("utf-8")).hexdigest()


def _offline_resource_deep_dive(
    resource: dict[str, Any],
    subtopic: dict[str, Any],
    question_lines: list[str],
    evidence_pack: str,
    source_digest: str,
) -> dict[str, Any]:
    title = resource.get("title") or "Untitled resource"
    summary = (resource.get("summary") or "").strip()
    notes = (resource.get("notes") or "").strip()
    overview = f"{title} sits inside '{subtopic.get('name') or 'this subtopic'}' and should be read as a standalone argument."
    if summary:
        overview += f" Summary signal: {summary[:320]}"
    evidence_notes = _extract_signal_sentences(evidence_pack, max_items=4)
    return {
        "evidence_notes": evidence_notes,
        "resource_overview": overview,
        "strongest_ideas": [summary[:220]] if summary else ["Extract the strongest thesis this source defends."],
        "assumptions": [notes[:220]] if notes else ["Identify hidden assumptions and boundary conditions."],
        "tensions_and_angles": ["Compare this source's stance against at least one contrasting resource."],
        "key_concepts": [],
        "writing_angles": ["Write a brief for and against the main claim in this resource."],
        "next_questions": question_lines[:5] or ["What evidence would most strengthen or weaken this resource?"],
        "source_digest": source_digest,
    }


def _build_evidence_pack(text: str, max_chars: int = 9000) -> str:
    cleaned = _normalize_for_prompt(text)
    if not cleaned:
        return ""
    n = len(cleaned)
    if n <= max_chars:
        return cleaned
    # Cover intro, middle, and late sections to reduce first-page bias.
    one = max_chars // 3
    windows = [
        cleaned[:one],
        cleaned[max(0, n // 2 - one // 2) : min(n, n // 2 + one // 2)],
        cleaned[max(0, n - one) :],
    ]
    parts = []
    labels = ["[early]", "[middle]", "[late]"]
    for i, w in enumerate(windows):
        chunk = w.strip()
        if chunk:
            parts.append(f"{labels[i]}\n{chunk}")
    return "\n\n".join(parts)[:max_chars]


def _normalize_for_prompt(text: str) -> str:
    lines = []
    for ln in (text or "").splitlines():
        s = " ".join(ln.strip().split())
        if len(s) < 2:
            continue
        lines.append(s)
    return "\n".join(lines).strip()


def _extract_signal_sentences(text: str, max_items: int = 4) -> list[str]:
    sents = []
    raw = _normalize_for_prompt(text).replace("?", ".").replace("!", ".")
    for part in raw.split("."):
        s = part.strip()
        if len(s) < 45:
            continue
        # Favor sentences with specific markers of claims/method/findings.
        score = 0
        low = s.lower()
        for marker in ("argue", "claim", "find", "result", "because", "therefore", "however", "method", "evidence"):
            if marker in low:
                score += 1
        sents.append((score, s))
    sents.sort(key=lambda x: (-x[0], -len(x[1])))
    out = []
    for _, sent in sents:
        out.append(sent[:220])
        if len(out) >= max_items:
            break
    return out
