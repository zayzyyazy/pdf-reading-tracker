"""
Optional OpenAI-backed helpers for the research workspace.
Falls back to simple heuristics when no API key is configured or the API is unavailable.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from typing import Any, Optional

import app.settings_store as settings_store
from app.pdf_reader import _repair_hyphen_space_artifacts, _repair_titlecase_word_splits


def _client():
    key = settings_store.effective_openai_key()
    if not key:
        return None
    from openai import OpenAI

    return OpenAI(api_key=key)


def _dedupe_text_chunks(chunks: list[str], max_items: int = 4) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for x in chunks:
        s = (x or "").strip()
        if len(s) < 20:
            continue
        key = " ".join(s.lower().split()[:18])
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) >= max_items:
            break
    return out


def _is_publisher_licence_or_notice(line: str) -> bool:
    """Green OA / repository / publisher strips that are not article substance."""
    low = (line or "").lower()
    needles = (
        "creative commons",
        "creative commons licence",
        "creative commons license",
        "cc-by",
        "cc by-nc",
        "cc by",
        "version of record",
        "author manuscript",
        "submitted for peer review",
        "accepted for publication after peer review",
        "re-use is limited",
        "reuse is limited",
        "licence for details of permitted",
        "license for details of permitted",
        "condition of access that users",
        "please refer to the published source",
        "document may not be the version of record",
        "users recognise and abide by the legal requirements",
        "permitted re-use",
        "permitted reuse",
    )
    if any(n in low for n in needles):
        return True
    if "[article]" in low and ("licence" in low or "license" in low or "commons" in low):
        return True
    if "personal use" in low and ("licence" in low or "license" in low or "commons" in low):
        return True
    if "please note that this document" in low and "version" in low:
        return True
    return False


def _strip_repository_banner_lines(s: str) -> str:
    """Remove repository routing and licence tails; keep substantive body when mixed into one line."""
    t = (s or "").strip()
    if not t:
        return ""
    low = t.lower().replace("\u2019", "'").replace("\u2018", "'")
    if "briefing papers" in low and ("qut" in low or "centre for justice" in low) and len(t) < 240:
        return ""
    if ("author's version" in low or "authors version" in low) and re.search(r"\(\d{4}\)", t):
        m = re.search(r"\(\d{4}\)\s*(.+)$", t)
        if m and len(m.group(1).strip()) > 45:
            t = m.group(1).strip()
            low = t.lower()
    cut = None
    for token in (
        "[article]",
        "creative commons licence",
        "creative commons license",
        "creative commons",
        "author manuscript versions",
        "version of record",
        "notice: please note that this document",
    ):
        i = low.find(token)
        if i != -1 and i >= 36:
            cut = i if cut is None else min(cut, i)
    if cut is not None:
        t = t[:cut].strip(" ;—-\t")
    return t


def _offline_opening_summary(evidence_snippet: str, max_chars: int = 920) -> str:
    """Prefer the start of the contiguous evidence (usually abstract/intro) for local summaries."""
    parts: list[str] = []
    n = 0
    for p in (evidence_snippet or "").splitlines():
        s = re.sub(r"^(ORIGINAL ARTICLE|REVIEW ARTICLE|BRIEF COMMUNICATION)\s+", "", p.strip(), flags=re.I).strip()
        s = re.sub(r"^(Editorial Introduction|Executive Summary|Research Summary)\s+", "", s, flags=re.I).strip()
        s = _strip_repository_banner_lines(s)
        if len(s) < 130 or not _quality_filter_line(s):
            continue
        if _is_boilerplate_line(s) or _is_probably_reference_entry(s) or _is_publisher_licence_or_notice(s):
            continue
        if re.match(r"^table\s+\d", s.lower()):
            continue
        parts.append(s)
        n += len(s) + 1
        if n >= max_chars:
            break
    return " ".join(parts)[:max_chars].strip()


def _offline_summary(ordered_text: str, evidence_snippet: str) -> dict[str, str]:
    """ordered_text must be reading-order normalized body; evidence_snippet may be a contiguous excerpt."""
    title = _infer_title_from_text(ordered_text)
    signals = _extract_paper_signals(evidence_snippet)
    points = [signals[k] for k in ("question", "framework", "method", "findings", "implications") if signals.get(k)]
    points = _dedupe_text_chunks(points, 5)
    opening_doc = _offline_opening_summary(ordered_text)
    opening_ev = _offline_opening_summary(evidence_snippet)
    opening = opening_doc if len(opening_doc) >= 380 else opening_ev
    if len(opening) >= 380:
        summary = opening
    elif points:
        summary = " ".join(points[:3])
    else:
        sents = _extract_signal_sentences(evidence_snippet, max_items=5)
        summary = " ".join(_dedupe_text_chunks(sents, 3))
    if not summary.strip() and (opening_doc or opening_ev):
        summary = opening_doc or opening_ev
    if not summary.strip():
        ev = (evidence_snippet or "").strip()
        summary = (ev[:500] + "…") if len(ev) > 500 else ev
    return {
        "title": _sanitize_generated_title(title, ordered_text),
        "summary": summary.strip() or "No text extracted.",
    }


def _fallback_questions(title: str, evidence_pack: str = "") -> list[str]:
    t = title or "this material"
    signals = _extract_paper_signals(evidence_pack)
    anchor = signals.get("findings") or signals.get("framework") or ""
    return [
        f"What exact problem does “{t[:80]}” claim prior literature misses, and how convincing is that gap framing?",
        "Which assumptions connect the framework to the findings, and which are explicit versus implicit?",
        f"Where could this claim break when moving from digital interaction to offline context: '{anchor[:90]}'?" if anchor else "Which argumentative step appears least supported by the source's own evidence?",
        "What focused follow-up study would most quickly test the paper's strongest practical implication?",
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
    ordered = _normalize_for_prompt(text or "")
    snippet = _build_evidence_pack(text or "", max_chars=max_chars)
    opening = _reading_order_prefix(ordered, max_chars=min(2400, max(1200, max_chars // 2 + 300)))
    client = _client()
    if not client:
        return _offline_summary(ordered, snippet)

    prompt = (
        "Read material from ONE source. The opening block is in original reading order (use it to infer the true title).\n"
        "The second block is a longer contiguous excerpt chosen for substantive sections; do not treat unrelated sentences as one argument.\n"
        "Infer the source's specific argument structure, not just topic keywords.\n"
        "Return JSON only with keys:\n"
        '- title: human-readable work title only (not a citation line, byline, date stamp, or disclaimer). 6-14 words when possible.\n'
        '- summary: 5-8 coherent sentences grounded in the evidence. Must include (when available): research question, framework, method/sample, central findings, implication.\n'
        "Rules:\n"
        "- Do not write generic academic filler or list-like fragments.\n"
        "- No copied sentence fragments or dangling citations.\n"
        "- Ignore Creative Commons, repository deposit, author-manuscript, and Version-of-Record licence text entirely.\n"
        "- If the text is partial/noisy, state uncertainty briefly instead of guessing.\n"
        f"\n--- OPENING (reading order) ---\n{opening}\n"
        f"\n--- SUBSTANTIVE EXCERPT ---\n{snippet}\n---\n"
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
            "title": _sanitize_generated_title((data.get("title") or "Untitled source").strip(), ordered or text),
            "summary": (data.get("summary") or "").strip(),
        }
    except (json.JSONDecodeError, Exception):
        return _offline_summary(ordered, snippet)


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
    structured_signals = _extract_paper_signals(evidence_pack)
    client = _client()
    if not client:
        return _offline_resource_deep_dive(resource, subtopic, question_lines, evidence_pack, source_digest)
    prompt = (
        "Create a serious deep dive for a single research resource.\n"
        "Ground claims in the provided title/summary/notes/questions/evidence pack.\n"
        "Avoid generic prose.\n"
        "Do not output instructions to the reader. Output concrete synthesis statements only.\n"
        "Ignore publisher/legal/copyright boilerplate and prioritize conceptual content.\n"
        "Ignore Creative Commons / repository / 'author version' / 'Version of Record' / licence-reuse notices entirely.\n"
        "Prioritize abstract, introduction, findings/results, discussion, and conclusion material.\n"
        "Paraphrase in your own words; avoid quoting source fragments longer than 8 words.\n"
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
        f"structured_signals: {json.dumps(structured_signals, ensure_ascii=True)}\n"
        f"evidence_pack:\n{evidence_pack}\n"
    )
    offline_payload = _offline_resource_deep_dive(resource, subtopic, question_lines, evidence_pack, source_digest)
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or "{}"
        data = json.loads(raw)
        return {
            "evidence_notes": _as_clean_list(data.get("evidence_notes"), offline_payload["evidence_notes"]),
            "resource_overview": _clean_label(
                str(data.get("resource_overview") or ""),
                offline_payload["resource_overview"],
                900,
            ),
            "strongest_ideas": _as_clean_list(data.get("strongest_ideas"), offline_payload["strongest_ideas"]),
            "assumptions": _as_clean_list(data.get("assumptions"), offline_payload["assumptions"]),
            "tensions_and_angles": _as_clean_list(data.get("tensions_and_angles"), offline_payload["tensions_and_angles"]),
            "key_concepts": _as_clean_list(data.get("key_concepts"), offline_payload["key_concepts"]),
            "writing_angles": _as_clean_list(data.get("writing_angles"), offline_payload["writing_angles"]),
            "next_questions": _as_clean_list(data.get("next_questions"), offline_payload["next_questions"]),
            "source_digest": source_digest,
        }
    except (json.JSONDecodeError, Exception):
        return offline_payload


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


def _substantive_body_paragraphs(evidence_pack: str, min_len: int = 180, cap: int = 6) -> list[str]:
    out: list[str] = []
    for p in (evidence_pack or "").splitlines():
        s = p.strip()
        if len(s) < min_len:
            continue
        if not _quality_filter_line(s):
            continue
        if _is_boilerplate_line(s) or _is_publisher_licence_or_notice(s) or _is_probably_reference_entry(s):
            continue
        out.append(s)
    out.sort(key=lambda x: -len(x))
    return out[:cap]


def _synthesize_strong_idea_from_body(paragraph: str) -> str:
    """Interpretive bullet from a substantive paragraph (offline): avoids pasting chopped fragments."""
    t = re.sub(r"\s+", " ", (paragraph or "").strip())
    t = _strip_repository_banner_lines(t).lstrip("\u201c\u201d\"'")
    parts = re.split(r"(?<=[.!?])\s+", t)
    pick = ""
    for seg in parts:
        seg = seg.strip()
        if len(seg) >= 72 and not _is_publisher_licence_or_notice(seg):
            pick = seg
            break
    if not pick:
        pick = t[:300].strip()
    for lead in ("Importantly, ", "However, ", "Therefore, ", "Thus, ", "Yet, ", "Further, ", "Moreover, "):
        if pick.lower().startswith(lead.lower()):
            pick = pick[len(lead) :].strip()
            break
    if len(pick) < 55:
        return "The piece connects legal, technical, and institutional threads in the extracted body (more text improves precision)."
    if pick[0].islower():
        return (f"The piece stresses that {pick[:210]}").rstrip(".") + "."
    return (f"The piece emphasizes that {pick[0].lower() + pick[1:]}")[:220].rstrip(".") + "."


def _offline_resource_deep_dive(
    resource: dict[str, Any],
    subtopic: dict[str, Any],
    question_lines: list[str],
    evidence_pack: str,
    source_digest: str,
) -> dict[str, Any]:
    title = resource.get("title") or "Untitled resource"
    summary = _normalize_for_prompt((resource.get("summary") or "").strip())
    notes = _normalize_for_prompt((resource.get("notes") or "").strip())
    body_paras = _substantive_body_paragraphs(evidence_pack, min_len=160, cap=6)
    evidence_notes = _stratified_passages_from_pack(evidence_pack, max_items=8)
    if len([e for e in evidence_notes if len(e) > 90]) < 2 and body_paras:
        evidence_notes = _dedupe_text_chunks([p[:400] for p in body_paras[:5]] + evidence_notes, 8)
    signals = _extract_paper_signals(evidence_pack)
    strongest = [_synthesize_strong_idea_from_body(p) for p in body_paras[:4]]
    if len([x for x in strongest if len(x) > 70]) < 2:
        sig_list = [signals[k] for k in ("framework", "method", "findings", "implications") if signals.get(k)]
        strongest = [_summarize_idea(s) for s in sig_list[:4] if s]
    if not strongest:
        strongest = evidence_notes[:3] if evidence_notes else _extract_signal_sentences(summary, max_items=3)
    if not strongest:
        strongest = ["The source appears partially extracted; core argumentative lines are limited."]

    if body_paras:
        lead_parts: list[str] = []
        for p in body_paras[:2]:
            seg = _paraphrase_claim(p[:480])
            if seg and seg[0].isalpha() and seg[0].islower():
                seg = seg[0].upper() + seg[1:]
            if seg:
                lead_parts.append(seg)
        lead = " ".join(lead_parts)
        overview = f"{title}. {lead}".strip()
        if len(overview) > 960:
            overview = overview[:957] + "…"
    else:
        overview_parts = [f"{title} advances a specific argument rather than a generic survey."]
        if signals.get("question"):
            overview_parts.append(f"It asks: {_paraphrase_claim(signals['question'])}.")
        if signals.get("framework"):
            overview_parts.append(f"It frames the analysis through {_paraphrase_claim(signals['framework'])}.")
        if signals.get("method"):
            overview_parts.append(f"The evidence base comes from {_paraphrase_claim(signals['method'])}.")
        if signals.get("findings"):
            overview_parts.append(f"Core finding: {_paraphrase_claim(signals['findings'])}.")
        if signals.get("implications"):
            overview_parts.append(f"Practical implication: {_paraphrase_claim(signals['implications'])}.")
        elif summary and len(summary) > 40:
            overview_parts.append(summary[:260])
        overview = " ".join(overview_parts)

    assumptions = _derive_assumptions(strongest, notes, [e for e in evidence_notes if not _is_publisher_licence_or_notice(e)])
    tensions = _derive_tensions(strongest, [e for e in evidence_notes if not _is_publisher_licence_or_notice(e)])
    writing_angles = _derive_writing_angles(strongest, tensions)
    next_q = _derive_next_questions(strongest, tensions, question_lines)
    key_concepts = _extract_key_concepts(evidence_pack)
    return {
        "evidence_notes": evidence_notes[:5],
        "resource_overview": overview,
        "strongest_ideas": strongest,
        "assumptions": assumptions,
        "tensions_and_angles": tensions,
        "key_concepts": key_concepts,
        "writing_angles": writing_angles,
        "next_questions": next_q,
        "source_digest": source_digest,
    }


def _reading_order_prefix(ordered: str, max_chars: int) -> str:
    parts: list[str] = []
    n = 0
    for ln in (ordered or "").splitlines():
        s = ln.strip()
        if not s:
            continue
        if n + len(s) + 1 > max_chars:
            break
        parts.append(s)
        n += len(s) + 1
    return "\n".join(parts).strip()


def _looks_like_reference_or_byline(s: str) -> bool:
    t = (s or "").strip()
    if not t:
        return True
    if "@" in t:
        return True
    low = t.lower()
    if low.startswith(("http://", "https://", "doi:", "received:", "accepted:", "published online")):
        return True
    if re.search(r"\bjournal of\b", low) and re.search(r"\bvol\.?\s*\d", low):
        return True
    if re.match(r"^[\w\s,\.&'\-]+\.\s*\(\s*20\d{2}\s*,\s*\d{1,2}\s+[a-z]+", low):
        return True
    if len(re.findall(r"\(\s*20\d{2}", t)) >= 2:
        return True
    if low.startswith(("lastly.", "finally.", "in conclusion,")):
        return True
    return False


def _looks_like_reference_block_line(s: str) -> bool:
    low = (s or "").lower().strip()
    if re.match(r"^\[\d+\]\s+[a-z]", low):
        return True
    if low.startswith(("references", "bibliography", "works cited")) and len(s) < 48:
        return True
    if len(re.findall(r"\d{4}\s*;\s*\d+", low)) >= 2 and "doi" in low:
        return True
    if sum(low.count(x) for x in (" et al.", " et al,", "pp.", "vol.")) >= 2 and len(s) < 220:
        return True
    return False


def _is_probably_reference_entry(s: str) -> bool:
    """Detect bibliography / citation lines so they are not used as body evidence or titles."""
    t = (s or "").strip()
    if len(t) < 42:
        return False
    low = t.lower()
    if "doi:" in low or "doi.org" in low or "http://" in low or "https://" in low:
        return True
    if re.search(r"\(20\d{2}\)", t) and t.count(",") >= 3:
        return True
    if re.search(r"\(19\d{2}\)", t) and ("press" in low or "books" in low or "journal" in low):
        return True
    if re.search(r"^[A-Z][a-z]+,\s+[A-Z]", t) and re.search(r"\((?:19|20)\d{2}\)", t) and ("&" in t or " and " in t):
        return True
    if " ed." in low or " eds." in low or "vol." in low or "pp." in low:
        return True
    if re.search(r"\b\d{1,2}\s*\(\s*\d+\s*\)\s*:\s*\d+", t):
        return True
    return False


def _reference_zone_start(paragraphs: list[str]) -> int:
    """First index where the document is likely references / bibliography (soft + hard)."""
    n = len(paragraphs)
    for i, p in enumerate(paragraphs):
        low = p.lower().strip().strip(":")
        if low in ("references", "bibliography", "works cited") and len(p) < 55:
            return i
    streak = 0
    for i, p in enumerate(paragraphs):
        if _is_probably_reference_entry(p):
            streak += 1
            if streak >= 5:
                return max(0, i - 4)
        else:
            streak = 0
    return n


def _contiguous_evidence_paragraphs(normalized: str, max_chars: int) -> list[str]:
    paragraphs = [p.strip() for p in normalized.splitlines() if p.strip()]
    if not paragraphs:
        return []
    ref_cut = _reference_zone_start(paragraphs)
    weights: list[float] = []
    current_section = "body"
    in_refs = False
    for i, p in enumerate(paragraphs):
        if i >= ref_cut:
            weights.append(-1e6)
            continue
        low = p.lower().strip().strip(":")
        sec = _detect_section(low)
        if sec and len(p) < 52:
            current_section = sec
        if low in ("references", "bibliography", "works cited") and len(p) < 44:
            in_refs = True
        if in_refs:
            weights.append(-1e6)
            continue
        if sec and len(p) < 52:
            weights.append(-45.0)
            continue
        if not _quality_filter_line(p):
            weights.append(-220.0)
            continue
        if (
            _is_boilerplate_line(p)
            or _looks_like_reference_block_line(p)
            or _is_probably_reference_entry(p)
            or _is_publisher_licence_or_notice(p)
        ):
            weights.append(-400.0)
            continue
        w = 1.0
        if current_section in {
            "abstract",
            "introduction",
            "framework",
            "method",
            "results",
            "discussion",
            "conclusion",
        }:
            w += 2.3
        lw = p.lower()
        for kw in (
            "argue",
            "find",
            "result",
            "suggest",
            "framework",
            "method",
            "sample",
            "however",
            "therefore",
            "examines",
            "investigate",
        ):
            if kw in lw:
                w += 0.55
        if any(k in lw for k in ("table", "figure", "appendix", "doi:", "http", "www.")):
            w -= 1.6
        if sum(ch.isdigit() for ch in p) > 14:
            w -= 0.9
        weights.append(w)

    n = len(paragraphs)
    best_local: list[str] = []
    best_sum = -1e18
    for i in range(n):
        ssum = 0.0
        chars = 0
        local: list[str] = []
        for k in range(i, n):
            wj = weights[k]
            pj = paragraphs[k]
            if wj <= -1e5:
                break
            if wj < -160:
                if not local:
                    continue
                break
            if chars + len(pj) + 1 > max_chars:
                break
            ssum += max(0.05, wj)
            chars += len(pj) + 1
            local.append(pj)
        if len(local) >= 2 and ssum > best_sum:
            best_sum = ssum
            best_local = local
    if len(best_local) < 2 and paragraphs:
        out: list[str] = []
        used = 0
        for j, pj in enumerate(paragraphs):
            if weights[j] < -1e5:
                break
            if not _quality_filter_line(pj) or _is_boilerplate_line(pj) or _is_publisher_licence_or_notice(pj):
                continue
            if used + len(pj) + 1 > max_chars:
                break
            out.append(pj)
            used += len(pj) + 1
            if len(out) >= 6:
                break
        return out
    return best_local


def _stratified_passages_from_pack(evidence_pack: str, max_items: int = 8) -> list[str]:
    """Sample passages in document order (no global re-sort) for offline evidence notes."""
    lines = [ln.strip() for ln in (evidence_pack or "").splitlines() if ln.strip()]
    if not lines:
        return []
    n = len(lines)
    out: list[str] = []
    seen: set[str] = set()
    for frac in (0.0, 0.08, 0.18, 0.32, 0.48, 0.62, 0.78, 0.9):
        i = min(n - 1, int(frac * (n - 1)))
        p = lines[i]
        key = " ".join(p.lower().split()[:10])
        if key in seen or len(p) < 100 or not _quality_filter_line(p):
            continue
        if _is_boilerplate_line(p) or _is_publisher_licence_or_notice(p) or _is_probably_reference_entry(p):
            continue
        seen.add(key)
        out.append(p[:420])
        if len(out) >= max_items:
            break
    if len(out) < 3:
        for p in lines:
            if len(p) < 100:
                continue
            if not _quality_filter_line(p):
                continue
            if _is_boilerplate_line(p) or _is_publisher_licence_or_notice(p) or _is_probably_reference_entry(p):
                continue
            key = " ".join(p.lower().split()[:10])
            if key in seen:
                continue
            seen.add(key)
            out.append(p[:420])
            if len(out) >= max_items:
                break
    return out


def _build_evidence_pack(text: str, max_chars: int = 9000) -> str:
    cleaned = _normalize_for_prompt(text)
    if not cleaned:
        return ""
    picked = _contiguous_evidence_paragraphs(cleaned, max_chars)
    if not picked:
        return cleaned[:max_chars]
    parts: list[str] = []
    budget = 0
    for p in picked:
        if budget + len(p) + 1 > max_chars:
            break
        parts.append(p)
        budget += len(p) + 1
    return "\n".join(parts).strip()


def _normalize_for_prompt(text: str) -> str:
    lines = []
    seen: dict[str, int] = {}
    raw_lines = []
    for ln in (text or "").splitlines():
        s = " ".join(ln.replace("\u00ad", "").strip().split())
        s = s.replace("\ufb01", "fi").replace("\ufb02", "fl")
        s = _repair_hyphen_space_artifacts(_repair_titlecase_word_splits(s))
        s = _strip_repository_banner_lines(s)
        if len(s) < 12:
            continue
        s = re.sub(
            r"([a-z\)])\s+(The findings|These findings|This study|The study|Results|Discussion|Conclusion)\b",
            r"\1. \2",
            s,
        )
        if len(s) < 2 or not _quality_filter_line(s):
            continue
        if _is_boilerplate_line(s) or _is_publisher_licence_or_notice(s):
            continue
        raw_lines.append(s)
        seen[s] = seen.get(s, 0) + 1
    repeat_threshold = max(3, int(len(raw_lines) * 0.12)) if raw_lines else 999999
    for s in raw_lines:
        # prune repeated running headers/footers
        if seen.get(s, 0) >= repeat_threshold and len(s) < 120:
            continue
        lines.append(s)
    paragraphs = _recover_paragraphs_for_ai(lines)
    return "\n".join(paragraphs).strip()


def _extract_signal_sentences(text: str, max_items: int = 4) -> list[str]:
    sents = []
    raw = _normalize_for_prompt(text).replace("\n", ". ")
    for part in re.split(r"[.!?]\s+", raw):
        s = " ".join(part.strip().split())
        if len(s) < 55:
            continue
        if not _quality_filter_line(s):
            continue
        if _is_boilerplate_line(s) or _is_publisher_licence_or_notice(s):
            continue
        # Favor sentences with specific markers of claims/method/findings.
        score = 0
        low = s.lower()
        for marker in (
            "argue",
            "claim",
            "find",
            "result",
            "because",
            "therefore",
            "however",
            "method",
            "focus group",
            "sample",
            "framework",
            "implication",
            "consent",
        ):
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


def _is_boilerplate_line(line: str) -> bool:
    low = line.lower()
    boiler = (
        "springer nature",
        "terms of use",
        "license",
        "all rights reserved",
        "copyright",
        "reprints and permissions",
        "publisher",
        "author accepted manuscript",
        "supplementary information",
        "doi:",
        "www.",
        "http://",
        "https://",
        "disclaim and waive",
        "implied warranties",
        "non-commercial use",
        "small scale, personal",
        "creative commons",
        "version of record",
        "author manuscript",
        "licence for details",
        "license for details",
        "condition of access",
    )
    if any(x in low for x in boiler):
        return True
    if len(line) > 20 and sum(ch.isalpha() for ch in line) < 8:
        return True
    return False


def _derive_assumptions(strongest: list[str], notes: str, evidence: list[str]) -> list[str]:
    base = strongest + evidence
    out = []
    for s in base[:4]:
        low = s.lower()
        claim = _paraphrase_claim(s)
        if "predict" in low or "correlat" in low:
            out.append(f"It assumes the reported metric relationship remains stable outside the sampled setting: {claim[:170]}.")
        elif "propose" in low or "replace" in low:
            out.append(f"It assumes institutions can implement the proposed replacement without losing decision quality: {claim[:170]}.")
        elif "tension" in low or "trade-off" in low:
            out.append(f"It assumes the highlighted trade-off is structural, not just an artifact of this study design: {claim[:170]}.")
        elif "because" in low:
            out.append(f"It assumes the implied causal chain holds beyond the examples discussed: {claim[:170]}.")
        else:
            out.append(f"It assumes this claim extends beyond the immediate cases in the paper: {claim[:170]}.")
    if notes:
        out.append(f"Author notes imply an additional framing assumption: {notes[:170]}")
    return out[:4] or ["The source assumes its benchmark or case framing is representative of the broader problem."]


def _derive_tensions(strongest: list[str], evidence: list[str]) -> list[str]:
    out = []
    pool = strongest + evidence
    if len(pool) >= 2:
        out.append(
            "A central tension is between "
            f"'{_paraphrase_claim(pool[0])[:90]}' and '{_paraphrase_claim(pool[1])[:90]}'."
        )
    for s in pool[:4]:
        low = s.lower()
        if "however" in low or "but" in low:
            out.append(f"The source flags an internal trade-off around {_paraphrase_claim(s)[:180]}.")
        if "failed" in low or "risk" in low:
            out.append(f"It acknowledges a vulnerability that complicates its main position: {_paraphrase_claim(s)[:180]}.")
    return out[:4] or ["The source advances a strong claim but leaves limits under-specified."]


def _derive_writing_angles(strongest: list[str], tensions: list[str]) -> list[str]:
    out = []
    if strongest:
        out.append(
            "A strong writing angle is to evaluate whether the paper's central claim survives in a different context: "
            f"{_paraphrase_claim(strongest[0])[:150]}"
        )
    if tensions:
        out.append(f"Another writing angle is to center critique on this tension: {tensions[0][:170]}")
    if len(strongest) > 1:
        out.append(f"The piece can contrast the main claim with this qualifier: {_paraphrase_claim(strongest[1])[:170]}")
    return out[:4] or ["The clearest writing path is to stress-test the source's strongest claim against an alternative mechanism."]


def _derive_next_questions(strongest: list[str], tensions: list[str], existing: list[str]) -> list[str]:
    out = []
    if strongest:
        out.append(f"What evidence would most directly falsify this line: {_paraphrase_claim(strongest[0])[:140]}?")
    if tensions:
        out.append(f"Which side of this tension is better supported in the source's own evidence: {tensions[0][:140]}?")
    out.extend(existing[:2])
    return out[:5] or ["What specific part of the source's argument would break first under a changed context?"]


def _summarize_idea(text: str) -> str:
    s = _paraphrase_claim(text)
    if s.lower().startswith("this creates"):
        s = s.replace("This creates", "A core implication is that", 1)
    if not s.endswith("."):
        s += "."
    return s[:220]


def _paraphrase_claim(text: str) -> str:
    head = _strip_repository_banner_lines((text or "").strip())
    s = _normalize_for_prompt(head).split("\n")[0].strip() if head else ""
    if not s:
        s = _normalize_for_prompt(text or "").split("\n")[0].strip()
    for lead in ("Importantly, ", "However, ", "Therefore, ", "Thus, ", "Yet, ", "Further, ", "Moreover, "):
        if s.lower().startswith(lead.lower()):
            s = s[len(lead) :].strip()
            break
    for lead in (
        "the paper argues that",
        "this paper argues that",
        "the author claims that",
        "a key finding is that",
        "the paper finds that",
    ):
        if s.lower().startswith(lead):
            s = s[len(lead) :].strip()
            break
    return s[:220]


def _section_weighted_passages(text: str, max_items: int = 10) -> list[str]:
    lines = [ln.strip() for ln in _normalize_for_prompt(text).splitlines() if ln.strip()]
    if not lines:
        return []
    current_section = "body"
    scored: list[tuple[float, str, str]] = []
    for ln in lines:
        low = ln.lower()
        sec = _detect_section(low)
        if sec:
            current_section = sec
            continue
        if len(ln) < 55:
            continue
        if not _quality_filter_line(ln):
            continue
        if _is_boilerplate_line(ln):
            continue
        score = 1.0
        if current_section in {"abstract", "introduction", "framework", "method", "results", "discussion", "conclusion"}:
            score += 2.0
        if any(
            k in low
            for k in (
                "argue",
                "claim",
                "find",
                "result",
                "therefore",
                "however",
                "suggest",
                "shows",
                "framework",
                "we use",
                "focus group",
                "consent",
            )
        ):
            score += 1.2
        if any(k in low for k in ("table", "figure", "appendix", "references", "et al.", "doi")):
            score -= 0.8
        if sum(ch.isdigit() for ch in ln) > 10:
            score -= 0.5
        scored.append((score, current_section, ln))
    scored.sort(key=lambda x: (-x[0], -len(x[2])))
    out: list[str] = []
    seen_roots: set[str] = set()
    section_quota: dict[str, int] = {"abstract": 2, "introduction": 3, "framework": 2, "method": 2, "results": 3, "discussion": 2, "conclusion": 2}
    section_counts: dict[str, int] = {}
    for _, sec, ln in scored:
        root = " ".join(ln.lower().split()[:8])
        if root in seen_roots:
            continue
        lim = section_quota.get(sec, 2)
        if section_counts.get(sec, 0) >= lim:
            continue
        seen_roots.add(root)
        section_counts[sec] = section_counts.get(sec, 0) + 1
        out.append(ln[:min(len(ln), 520)])
        if len(out) >= max_items:
            break
    return out


def _detect_section(line_low: str) -> str:
    if line_low.startswith("abstract"):
        return "abstract"
    if line_low.startswith("introduction") or line_low.startswith("background"):
        return "introduction"
    if "framework" in line_low or line_low.startswith("theory") or line_low.startswith("theoretical"):
        return "framework"
    if line_low.startswith("methods") or line_low.startswith("method") or "materials and methods" in line_low:
        return "method"
    if line_low.startswith("results") or line_low.startswith("findings"):
        return "results"
    if line_low.startswith("discussion"):
        return "discussion"
    if line_low.startswith("conclusion"):
        return "conclusion"
    return ""


def _candidates_after_year_title_tail(s: str, line_index: int) -> list[str]:
    """
    When PDF recovery merges 'Keywords … Received … 2026 REAL TITLE', pull the segment
    after the last calendar-year token before an uppercase word (title start).
    """
    low = (s or "").lower()
    allow = line_index <= 4 or "keywords" in low or "received:" in low or "accepted:" in low or "author(s)" in low
    if not allow or len(s) < 40:
        return []
    out: list[str] = []
    for m in re.finditer(r"(?:19|20)\d{2}\s+(?=[A-Z])", s):
        tail = s[m.end() :].strip(" \t-—")
        if len(tail) < 28:
            continue
        if len(tail) > 400:
            continue
        if len(tail) > 220:
            tail = tail[:220].rsplit(" ", 1)[0].strip()
        if 28 <= len(tail) <= 240:
            out.append(tail)
    return out[-1:] if out else []


def _isolate_title_from_runon(s: str) -> str:
    """Pull a likely title from a paragraph that merged title + body (common in PDF recovery)."""
    t = " ".join((s or "").strip().split())
    if not t:
        return ""
    for sep in (
        " The theoretical",
        " This study",
        " The study",
        " The present",
        " The authors",
        " We ",
        " It ",
        " Camming The ",
        " The integration",
        " Exploring ",
    ):
        idx = t.find(sep)
        if 28 <= idx <= 160:
            return t[:idx].strip(" ,.—-")
    m = re.search(r"\s+[A-Z][a-z]+\s+[A-Z]\.\s+[A-Z][a-z]+", t)
    if m and m.start() >= 32:
        return t[: m.start()].strip(" ,.—-")
    return t


def _trim_title_affiliation(title: str) -> str:
    t = " ".join((title or "").strip().split())
    if "@" in t:
        t = t.split("@", 1)[0].strip(" ,.;—-\t")
    t = re.sub(r"([A-Za-z])1\s+(?=[A-Z])", r"\1 ", t)
    t = re.sub(r"\s+\d+\s+[A-Z][a-z]+\s+[A-Z][a-z]+(\s+[a-z][a-z0-9._-]+)?\s*$", "", t, flags=re.I).strip()
    t = re.sub(r"\s+\d+\s+[A-Z][^.!?]{0,90}$", "", t).strip()
    if ":" in t and len(t) > 92:
        a, b = t.split(":", 1)
        bw = b.strip().split()
        if len(bw) > 4 and len(a.strip()) > 28:
            t = f"{a.strip()}: {' '.join(bw[:2])}"
    if "?" in t:
        t = re.sub(r"\s+(?:of\s+)?Cam Models\s*$", "", t, flags=re.I).strip()
    return t[:160].rstrip(" ,.;—")


def _title_candidates_from_line(ln: str) -> list[str]:
    out = [ln]
    if "@" in ln:
        left = ln.split("@", 1)[0].strip(" ,.;—-\t")
        if len(left) >= 16:
            out.insert(0, left)
    return out


def _score_title_line(t: str) -> float:
    raw = " ".join((t or "").strip().split())
    if not raw or len(raw) < 12:
        return -100.0
    if "@" in raw:
        raw = raw.split("@", 1)[0].rstrip(" ,.;—-\t")
    s = raw[:200].rstrip(" ,.;—")
    low = s.lower()
    if not s or len(s) < 12:
        return -100.0
    if re.match(r"^\d+\s+[A-Za-z]", low):
        return -95.0
    if "school of" in low and "university" in low:
        return -95.0
    if re.match(r"^rq\s*\d", low):
        return -95.0
    if re.match(r"^(age|gender|relationship status|education|employment|residency)\s*,", low):
        return -70.0
    if re.match(r"^table\s+\d", low):
        return -85.0
    if any(
        x in low
        for x in (
            "straße",
            "sherbrooke",
            "bismarck",
            "declarations",
            "consent to participate",
            "informed consent was obtained",
            "competing interests",
            "funding:",
            "availability of data",
        )
    ):
        return -95.0
    if re.search(r"\buniversity\b.*\b(germany|canada|montreal|duisburg)\b", low):
        return -95.0
    if _looks_like_reference_or_byline(s) or _is_boilerplate_line(s):
        return -100.0
    if low.startswith(("original article", "review article", "abstract")):
        return -55.0
    if low.startswith("keywords ") and "received:" in low:
        return -35.0
    if any(low.startswith(p) for p in ("the ", "this ", "we ", "our ", "authors ", "using ", "while ", "although ")):
        return -35.0
    if low.startswith(("abstract", "introduction", "keywords", "accepted:", "received:", "published ", "doi:")):
        return -80.0
    if "disclaim" in low or "warranties" in low or "all parties" in low:
        return -100.0
    if low in {"abstract", "introduction", "results", "discussion", "conclusion", "findings", "original article"}:
        return -60.0
    score = 0.0
    if s[0].isalpha() and s[0].islower():
        score -= 52.0
    if 38 <= len(s) <= 130:
        score += 18.0
    elif 24 <= len(s) < 38:
        score += 8.0
    words = s.split()
    if len(words) >= 5:
        score += 8.0
    if ":" in s and len(s) < 165:
        score += 10.0
    if len(raw) > 200 and ":" not in raw[:140]:
        score -= 18.0
    if s.count("&") >= 2 and "?" not in s:
        score -= 28.0
    if "?" in s and 40 <= len(s) <= 200:
        score += 12.0
    score -= min(35.0, float(len(re.findall(r"\(\s*20\d{2}\)", s))) * 12.0)
    score -= min(18.0, float(len(re.findall(r"\b20\d{2}\b", s))) * 3.0)
    if low.startswith(("http://", "https://")):
        return -100.0
    return score


def _infer_title_from_text(text: str) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return "Untitled source"
    best = ""
    best_score = -1e9
    for i, ln in enumerate(lines[:85]):
        extra_tails = _candidates_after_year_title_tail(ln, i)
        for cand in list(_title_candidates_from_line(ln)) + extra_tails:
            for variant in {cand, _isolate_title_from_runon(cand)}:
                if not variant:
                    continue
                sc = _score_title_line(variant)
                if sc > best_score:
                    best_score = sc
                    best = variant
        if i + 1 < len(lines):
            merged = f"{lines[i]} {lines[i+1]}".strip()
            if 26 <= len(merged) <= 195:
                sc = _score_title_line(merged) + 2.5
                if sc > best_score:
                    best_score = sc
                    best = merged
    if best_score >= 5.0 and best:
        return _trim_title_affiliation(best)
    for ln in lines[:40]:
        low = ln.lower()
        if low in {"abstract", "introduction", "results", "discussion", "conclusion", "findings", "original article"}:
            continue
        if _is_boilerplate_line(ln) or _looks_like_reference_or_byline(ln):
            continue
        first = ln.split()[0].lower() if ln.split() else ""
        if first in {"the", "this", "we", "our"}:
            continue
        if 20 <= len(ln) <= 160 and _score_title_line(ln) >= 6.0:
            return _trim_title_affiliation(ln)
    return _trim_title_affiliation(best) if best else "Untitled source"


def _sanitize_generated_title(title: str, source_text: str) -> str:
    t = " ".join((title or "").strip().split())
    if not t:
        return _infer_title_from_text(source_text)
    low = t.lower()
    bad_starts = (
        "the paper ",
        "this paper ",
        "the author ",
        "a key finding",
        "we examine",
        "we propose",
        "all parties ",
    )
    if any(low.startswith(bs) for bs in bad_starts) or len(t) > 160:
        return _infer_title_from_text(source_text)
    if _looks_like_reference_or_byline(t) or _is_boilerplate_line(t):
        return _infer_title_from_text(source_text)
    if "disclaim" in low or "implied warranties" in low:
        return _infer_title_from_text(source_text)
    if _score_title_line(t) < 3.0:
        return _infer_title_from_text(source_text)
    return _trim_title_affiliation(t)


def _extract_key_concepts(text: str) -> list[str]:
    corpus = _normalize_for_prompt(text)
    sentences = _extract_signal_sentences(corpus, max_items=18)
    phrase_counts: Counter[str] = Counter()
    phrase_sentence: dict[str, str] = {}
    patterns = [
        r"\b([A-Za-z][A-Za-z\-]{3,}(?:\s+[A-Za-z][A-Za-z\-]{2,}){0,3}\s+(?:framework|theory|market|consent|brokering|broker|discourse|scripts?|implication|violence|culture|method))\b",
        r"\b((?:focus group|focus groups|participants|sample of \d+|dataset|sexual market framework|digital brokering))\b",
    ]
    for s in sentences:
        low = s.lower()
        for pat in patterns:
            for m in re.finditer(pat, s):
                p = m.group(1).strip().lower()
                p = re.sub(r"^(that|this|these|those|their|its)\s+", "", p)
                if not _is_valid_concept_phrase(p):
                    continue
                phrase_counts[p] += 1
                phrase_sentence[p] = s
    out = []
    for phrase, _ in phrase_counts.most_common(10):
        sent = phrase_sentence.get(phrase, "")
        why = _infer_concept_role(phrase, sent)
        out.append(f"{phrase}: {why}")
        if len(out) >= 6:
            break
    if not out:
        signals = _extract_paper_signals(corpus)
        for key, val in signals.items():
            if not val:
                continue
            lead = " ".join(val.split()[:5]).lower()
            if lead.startswith("for example") or lead.startswith("for instance"):
                continue
            if len(lead.split()) >= 2:
                out.append(f"{lead}: extracted from the paper's {key} signal")
            if len(out) >= 5:
                break
    return out


def _quality_filter_line(line: str) -> bool:
    s = (line or "").strip()
    if not s:
        return False
    if len(s) < 25:
        low_short = s.lower().strip()
        if low_short in (
            "original article",
            "review article",
            "brief communication",
            "editorial",
        ):
            return True
        return False
    alpha = sum(ch.isalpha() for ch in s)
    if alpha < max(10, int(len(s) * 0.45)):
        return False
    if len(re.findall(r"\b(?:et al|vol|no|pp)\b", s.lower())) >= 2:
        return False
    if re.search(r"\(\d{4}\)", s) and len(s) < 60:
        return False
    return True


def _looks_like_section_heading(line: str) -> bool:
    low = line.lower().strip().strip(":")
    if low in {"abstract", "introduction", "background", "methods", "method", "results", "findings", "discussion", "conclusion", "references"}:
        return True
    if re.match(r"^\d+(\.\d+)*\s+[A-Za-z]", line):
        return True
    return False


def _recover_paragraphs_for_ai(lines: list[str]) -> list[str]:
    paragraphs: list[str] = []
    current = ""
    for raw in lines:
        ln = raw.strip()
        if not ln:
            continue
        if _looks_like_section_heading(ln):
            if current:
                paragraphs.append(current.strip())
                current = ""
            paragraphs.append(ln)
            continue
        if not current:
            current = ln
            continue
        if current.endswith("-") and ln and ln[0].islower():
            current = current[:-1] + ln
            continue
        if current[-1] in ".!?":
            paragraphs.append(current.strip())
            current = ln
            continue
        if current[-1] in ":;" and len(current) > 110:
            paragraphs.append(current.strip())
            current = ln
            continue
        current = f"{current} {ln}"
    if current:
        paragraphs.append(current.strip())
    deduped = []
    seen = set()
    for p in paragraphs:
        key = " ".join(p.lower().split()[:12])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(p)
    return deduped


def _extract_paper_signals(text: str) -> dict[str, str]:
    sentences = _extract_signal_sentences(text, max_items=24)
    return {
        "question": _pick_best_sentence(sentences, ("ask", "question", "examines", "investigate", "we examine", "we ask", "why", "how")),
        "framework": _pick_best_sentence(sentences, ("framework", "theory", "lens", "market", "conceptual", "broker", "sexual market", "digitally")),
        "method": _pick_best_sentence(
            sentences,
            ("method", "sample", "participants", "focus group", "interview", "survey", "n=", "dataset", "data consist", "data consists"),
        ),
        "findings": _pick_best_sentence(sentences, ("find", "shows", "suggest", "identify", "results", "three", "forms")),
        "implications": _pick_best_sentence(sentences, ("implication", "prevention", "practice", "should", "recommend", "policy", "must")),
    }


def _pick_best_sentence(sentences: list[str], markers: tuple[str, ...]) -> str:
    best = ""
    best_score = 0
    for s in sentences:
        low = s.lower()
        score = sum(1 for m in markers if m in low)
        if score > best_score:
            best = s
            best_score = score
    return best[:240]


def _is_valid_concept_phrase(phrase: str) -> bool:
    toks = [t for t in re.split(r"\s+", phrase.strip().lower()) if t]
    while toks and toks[0] in {"that", "this", "these", "those", "their", "its"}:
        toks = toks[1:]
    if len(toks) < 2 or len(toks) > 5:
        return False
    bad = {
        "suggested",
        "finding",
        "results",
        "paper",
        "study",
        "author",
        "their",
        "therefore",
        "however",
        "because",
        "important",
        "strong",
        "analytic",
        "consists",
        "florida",
        "amazon",
        "shocked",
        "child",
        "photo",
        "resembling",
        "september",
        "mom",
        "sold",
        "doll",
        "daily",
        "news",
    }
    if any(t in bad for t in toks):
        return False
    if all(len(t) <= 3 for t in toks):
        return False
    return True


def _infer_concept_role(phrase: str, sentence: str) -> str:
    low = (sentence or "").lower()
    if "framework" in low or "lens" in low or "theory" in low:
        return "organizes the paper's explanatory frame"
    if "method" in low or "focus group" in low or "sample" in low:
        return "anchors how evidence is gathered"
    if "consent" in phrase or "consent" in low:
        return "defines how the argument handles negotiation and boundaries"
    if "implication" in low or "prevention" in low or "should" in low:
        return "drives the paper's practical takeaway"
    return "functions as a recurring idea in the core argument"
