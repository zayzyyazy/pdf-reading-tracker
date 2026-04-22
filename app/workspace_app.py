"""
Local research workspace — FastAPI + Jinja2 + SQLite.
Run from repo root: python -m uvicorn app.workspace_app:app --reload --port 8765
"""
from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import research_ai
from app import research_db as db
from app import settings_store
from app.pdf_reader import extract_text_from_docx, extract_text_from_pdf, extract_text_from_txt

def _workspace_base_dir() -> str:
    r = os.environ.get("RESEARCH_WORKSPACE_ROOT")
    if r:
        return os.path.abspath(r)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


BASE_DIR = _workspace_base_dir()
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates", "workspace")
STATIC_DIR = os.path.join(BASE_DIR, "static", "workspace")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Research Workspace", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)


@app.middleware("http")
async def attach_ai_status(request: Request, call_next):
    request.state.ai_nav = settings_store.public_nav_hint()
    request.state.ai_status = settings_store.openai_status()
    return await call_next(request)


def _extract_uploaded_text(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return extract_text_from_pdf(path) or ""
    if ext == ".txt":
        return extract_text_from_txt(path) or ""
    if ext == ".docx":
        return extract_text_from_docx(path) or ""
    return ""


ALLOWED_INTAKE_EXT = {".pdf", ".txt", ".docx"}


def ingest_resource_bytes(
    subtopic_id: str,
    filename: str,
    content: bytes,
    title: str = "",
    summary: str = "",
    notes: str = "",
    source_type: str = "",
    placement_note: str = "",
) -> str:
    """
    Save bytes as a resource under subtopic_id, extract text, fill title/summary when empty.
    Returns new resource id.
    """
    if not db.get_subtopic(subtopic_id):
        raise HTTPException(404, "Subtopic not found")
    raw_name = os.path.basename(filename or "upload")
    ext = os.path.splitext(raw_name)[1].lower()
    if ext not in ALLOWED_INTAKE_EXT:
        raise HTTPException(400, f"Unsupported file type {ext or '(none)'}. Use PDF, TXT, or DOCX.")
    stype = (source_type or "").strip() or research_ai.infer_source_type(raw_name)
    initial_title = (title or "").strip() or raw_name
    rid = db.create_resource(
        subtopic_id,
        initial_title,
        stype,
        None,
        (summary or "").strip(),
        (notes or "").strip(),
        placement_note=(placement_note or "").strip(),
    )
    stored = f"{rid}{ext}"
    dest = os.path.join(db.UPLOADS_DIR, stored)
    os.makedirs(db.UPLOADS_DIR, exist_ok=True)
    with open(dest, "wb") as f:
        f.write(content)
    db.set_resource_file_path(rid, os.path.relpath(dest, BASE_DIR))
    text = _extract_uploaded_text(dest)
    if text.strip():
        meta = research_ai.summarize_for_resource(text, max_chars=12000)
        new_title = (title or "").strip() or meta.get("title") or initial_title
        new_summary = (summary or "").strip() or meta.get("summary") or ""
        db.update_resource(rid, title=new_title, summary=new_summary, source_type=stype)
    return rid


@app.get("/")
def dashboard(request: Request):
    cats = db.list_categories()
    enriched = []
    for c in cats:
        counts = db.category_counts(c["id"])
        enriched.append({**c, "counts": counts})
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "title": "Dashboard",
            "categories": enriched,
            "recent_resources": db.recent_resources(8),
            "recent_writings": db.recent_writings(6),
            "open_questions": db.open_questions_globally(12),
            "subtopics_flat": db.list_subtopics_for_intake(),
            "has_subtopics": bool(db.list_subtopics_for_intake()),
        },
    )


@app.get("/settings")
def settings_page(request: Request):
    st = settings_store.openai_status()
    saved = settings_store.get_saved_openai_key()
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "title": "Settings",
            "ai": st,
            "has_saved_key": bool(saved),
        },
    )


@app.post("/settings/openai")
def settings_openai_save(api_key: str = Form("")):
    key = (api_key or "").strip()
    if not key:
        return RedirectResponse(url="/settings", status_code=303)
    settings_store.set_openai_api_key(key)
    return RedirectResponse(url="/settings", status_code=303)


@app.post("/settings/openai/remove")
def settings_openai_remove():
    settings_store.clear_openai_api_key()
    return RedirectResponse(url="/settings", status_code=303)


@app.get("/intake")
def intake_page(request: Request, subtopic_id: str = ""):
    subs = db.list_subtopics_for_intake()
    st = settings_store.openai_status()
    pre = (subtopic_id or "").strip()
    if pre and not db.get_subtopic(pre):
        pre = ""
    return templates.TemplateResponse(
        request,
        "intake.html",
        {
            "title": "Add resources",
            "subtopics": subs,
            "ai": st,
            "has_subtopics": len(subs) > 0,
            "prefill_subtopic": pre,
        },
    )


@app.post("/intake")
async def intake_post(request: Request):
    form = await request.form()
    subtopic_choice = (form.get("subtopic_id") or "").strip()
    uploads = list(form.getlist("files"))
    if not uploads:
        one = form.get("file")
        if one is not None:
            uploads = [one]
    if not uploads:
        raise HTTPException(400, "No files received")

    subs = db.list_subtopics_for_intake()
    if not subs:
        raise HTTPException(400, "Create at least one category and subtopic before adding files.")

    last_rid: Optional[str] = None

    for up in uploads:
        if not hasattr(up, "read"):
            continue
        raw_name = os.path.basename(getattr(up, "filename", None) or "file")
        ext = os.path.splitext(raw_name)[1].lower()
        if ext not in ALLOWED_INTAKE_EXT:
            continue
        content = await up.read()
        if not content:
            continue

        os.makedirs(db.UPLOADS_DIR, exist_ok=True)
        tmp = os.path.join(db.UPLOADS_DIR, f"_intake_{uuid.uuid4().hex}{ext}")
        text = ""
        try:
            with open(tmp, "wb") as f:
                f.write(content)
            text = _extract_uploaded_text(tmp)
            if not text.strip() and ext == ".txt":
                try:
                    text = content.decode("utf-8", errors="ignore")
                except Exception:
                    text = ""
        finally:
            if os.path.isfile(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

        sid = subtopic_choice if subtopic_choice and db.get_subtopic(subtopic_choice) else ""
        placement_note = ""
        if not sid:
            sid, placement_note = _auto_route_subtopic_for_resource(text, raw_name)
        rid = ingest_resource_bytes(sid, raw_name, content, placement_note=placement_note)
        last_rid = rid

    if not last_rid:
        raise HTTPException(400, "No supported files (PDF, TXT, DOCX) were processed.")

    return RedirectResponse(url=f"/resources/{last_rid}", status_code=303)


@app.post("/categories/new")
def category_create(name: str = Form(...), description: str = Form("")):
    if not name.strip():
        raise HTTPException(400, "Name required")
    cid = db.create_category(name, description)
    return RedirectResponse(url=f"/categories/{cid}", status_code=303)


@app.get("/categories/{cat_id}")
def category_detail(request: Request, cat_id: str):
    cat = db.get_category(cat_id)
    if not cat:
        raise HTTPException(404, "Category not found")
    subs = db.list_subtopics_for_category(cat_id, None)
    subs_enriched = []
    for s in subs:
        cts = db.subtopic_counts(s["id"])
        subs_enriched.append({**s, "counts": cts})
    titles = []
    for s in subs:
        for r in db.list_resources_for_subtopic(s["id"]):
            titles.append(r["title"])
    return templates.TemplateResponse(
        request,
        "category.html",
        {
            "title": cat["name"],
            "category": cat,
            "counts": db.category_counts(cat_id),
            "subtopics": subs_enriched,
            "suggestions": research_ai.suggest_subtopic_names(cat["name"], titles),
        },
    )


@app.post("/categories/{cat_id}/subtopics/new")
def subtopic_create_root(
    cat_id: str,
    name: str = Form(...),
    research_field: str = Form(""),
    topic_summary: str = Form(""),
):
    if not db.get_category(cat_id):
        raise HTTPException(404)
    if not name.strip():
        raise HTTPException(400, "Name required")
    sid = db.create_subtopic(cat_id, name, None, research_field, topic_summary)
    return RedirectResponse(url=f"/subtopics/{sid}", status_code=303)


@app.get("/subtopics/{sid}")
def subtopic_detail(request: Request, sid: str):
    st = db.get_subtopic(sid)
    if not st:
        raise HTTPException(404, "Subtopic not found")
    cat = db.get_category(st["category_id"])
    breadcrumb = db.subtopic_breadcrumb(sid)
    children = db.list_child_subtopics(sid)
    children_e = [{**c, "counts": db.subtopic_counts(c["id"])} for c in children]
    resources = db.list_resources_for_subtopic(sid)
    questions = db.list_questions_for_subtopic(sid)
    writings = db.list_writings_for_subtopic(sid)
    deep_dive = db.get_deep_dive_for_subtopic(sid)
    current_digest = research_ai.deep_dive_source_digest(st, resources, questions)
    deep_dive_stale = bool(deep_dive and deep_dive.get("source_digest") != current_digest)
    return templates.TemplateResponse(
        request,
        "subtopic.html",
        {
            "title": st["name"],
            "category": cat,
            "subtopic": st,
            "breadcrumb": breadcrumb,
            "children": children_e,
            "counts": db.subtopic_counts(sid),
            "resources": resources,
            "questions": questions,
            "writings": writings,
            "deep_dive": deep_dive,
            "deep_dive_stale": deep_dive_stale,
        },
    )


@app.post("/subtopics/{sid}/children/new")
def subtopic_create_child(
    sid: str,
    name: str = Form(...),
    research_field: str = Form(""),
    topic_summary: str = Form(""),
):
    parent = db.get_subtopic(sid)
    if not parent:
        raise HTTPException(404)
    if not name.strip():
        raise HTTPException(400, "Name required")
    nid = db.create_subtopic(parent["category_id"], name, sid, research_field, topic_summary)
    return RedirectResponse(url=f"/subtopics/{nid}", status_code=303)


@app.post("/subtopics/{sid}/deep-dive/generate")
def subtopic_generate_deep_dive(sid: str):
    st = db.get_subtopic(sid)
    if not st:
        raise HTTPException(404, "Subtopic not found")
    resources = db.list_resources_for_subtopic(sid)
    questions = db.list_questions_for_subtopic(sid)
    payload = research_ai.build_deep_dive(st, resources, questions)
    db.upsert_deep_dive_for_subtopic(sid, payload, source_digest=payload.get("source_digest", ""))
    return RedirectResponse(url=f"/subtopics/{sid}", status_code=303)


@app.get("/resources/new")
def resource_new_form(request: Request, subtopic_id: str):
    st = db.get_subtopic(subtopic_id)
    if not st:
        raise HTTPException(404)
    cat = db.get_category(st["category_id"])
    return templates.TemplateResponse(
        request,
        "resource_new.html",
        {
            "title": "Add resource",
            "category": cat,
            "subtopic": st,
            "breadcrumb": db.subtopic_breadcrumb(subtopic_id),
        },
    )


@app.post("/resources/new")
async def resource_create(
    subtopic_id: str = Form(...),
    title: str = Form(""),
    source_type: str = Form("pdf"),
    summary: str = Form(""),
    notes: str = Form(""),
    file: Optional[UploadFile] = File(default=None),
):
    if not db.get_subtopic(subtopic_id):
        raise HTTPException(404)
    if file and file.filename:
        raw_name = os.path.basename(file.filename)
        content = await file.read()
        rid = ingest_resource_bytes(
            subtopic_id,
            raw_name,
            content,
            title=title,
            summary=summary,
            notes=notes,
            source_type=source_type,
        )
        return RedirectResponse(url=f"/resources/{rid}", status_code=303)
    rid = db.create_resource(
        subtopic_id,
        (title or "").strip() or "Untitled resource",
        source_type or "other",
        None,
        (summary or "").strip(),
        (notes or "").strip(),
    )
    return RedirectResponse(url=f"/resources/{rid}", status_code=303)


def _auto_route_subtopic_for_resource(text: str, filename: str) -> tuple[str, str]:
    cats = db.list_categories()
    subs = db.list_subtopics_for_intake()
    decision = research_ai.decide_resource_placement(text, filename, cats, subs)
    action = decision.get("action")
    if action == "existing_subtopic":
        sid = decision.get("existing_subtopic_id", "")
        st = db.get_subtopic(sid)
        if st:
            return sid, f"Joined existing subtopic: {st.get('name')}. {decision.get('reason', '').strip()}".strip()
    if action == "new_subtopic":
        cat_id = decision.get("existing_category_id", "")
        if db.get_category(cat_id):
            sub_name = (decision.get("subtopic_name") or "").strip() or "Core threads"
            sid = db.create_subtopic(
                cat_id,
                sub_name,
                None,
                decision.get("research_field", ""),
                decision.get("topic_summary", ""),
            )
            cat = db.get_category(cat_id)
            return sid, f"Created new subtopic '{sub_name}' in category '{cat.get('name')}'."
    if action == "new_category":
        category_name = (decision.get("category_name") or "").strip() or "New research area"
        category_desc = (decision.get("category_description") or "").strip()
        cat_id = db.create_category(category_name, category_desc)
        sub_name = (decision.get("subtopic_name") or "").strip() or "Core threads"
        sid = db.create_subtopic(
            cat_id,
            sub_name,
            None,
            decision.get("research_field", ""),
            decision.get("topic_summary", ""),
        )
        return sid, f"Created new category '{category_name}' and new subtopic '{sub_name}'."
    first_sid = db.first_subtopic_id()
    if not first_sid:
        raise HTTPException(400, "No subtopic available for intake placement.")
    return first_sid, "Placed in first available subtopic as fallback."


@app.get("/resources/{rid}")
def resource_detail(request: Request, rid: str):
    r = db.get_resource(rid)
    if not r:
        raise HTTPException(404)
    st = db.get_subtopic(r["subtopic_id"])
    if not st:
        raise HTTPException(404)
    cat = db.get_category(st["category_id"])
    qs = db.list_questions_for_resource(rid)
    resource_deep_dive = db.get_resource_deep_dive(rid)
    resource_digest = research_ai.resource_deep_dive_source_digest(r, qs)
    resource_deep_dive_stale = bool(resource_deep_dive and resource_deep_dive.get("source_digest") != resource_digest)
    return templates.TemplateResponse(
        request,
        "resource_detail.html",
        {
            "title": r["title"],
            "category": cat,
            "subtopic": st,
            "breadcrumb": db.subtopic_breadcrumb(st["id"]),
            "resource": r,
            "questions": qs,
            "resource_deep_dive": resource_deep_dive,
            "resource_deep_dive_stale": resource_deep_dive_stale,
        },
    )


@app.post("/resources/{rid}/edit")
def resource_edit(
    rid: str,
    title: str = Form(...),
    source_type: str = Form(...),
    summary: str = Form(""),
    notes: str = Form(""),
):
    if not db.get_resource(rid):
        raise HTTPException(404)
    db.update_resource(rid, title=title, source_type=source_type, summary=summary, notes=notes)
    return RedirectResponse(url=f"/resources/{rid}", status_code=303)


@app.post("/resources/{rid}/deep-dive/generate")
def resource_generate_deep_dive(rid: str):
    r = db.get_resource(rid)
    if not r:
        raise HTTPException(404)
    st = db.get_subtopic(r["subtopic_id"])
    if not st:
        raise HTTPException(404)
    qs = db.list_questions_for_resource(rid)
    excerpt = ""
    if r.get("file_path"):
        fp = os.path.join(BASE_DIR, r["file_path"])
        if os.path.isfile(fp):
            excerpt = _extract_uploaded_text(fp)
    payload = research_ai.build_resource_deep_dive(r, st, qs, excerpt=excerpt)
    db.upsert_resource_deep_dive(rid, payload, source_digest=payload.get("source_digest", ""))
    return RedirectResponse(url=f"/resources/{rid}", status_code=303)


@app.post("/resources/{rid}/questions/generate")
def resource_generate_questions(rid: str):
    r = db.get_resource(rid)
    if not r:
        raise HTTPException(404)
    st = db.get_subtopic(r["subtopic_id"])
    if not st:
        raise HTTPException(404)
    extra = ""
    if r.get("file_path"):
        fp = os.path.join(BASE_DIR, r["file_path"])
        if os.path.isfile(fp):
            extra = _extract_uploaded_text(fp)
    qs = research_ai.generate_questions(r.get("title") or "", r.get("summary") or "", extra)
    db.insert_questions_bulk(st["id"], rid, qs)
    return RedirectResponse(url=f"/resources/{rid}", status_code=303)


@app.post("/questions/new")
def question_create(
    subtopic_id: str = Form(...),
    body: str = Form(...),
    resource_id: str = Form(""),
    redirect_to: str = Form("/"),
):
    if not db.get_subtopic(subtopic_id):
        raise HTTPException(404)
    if not body.strip():
        raise HTTPException(400)
    rid = resource_id.strip() or None
    db.create_question(subtopic_id, body, rid, is_main=False)
    return RedirectResponse(url=redirect_to or "/", status_code=303)


@app.post("/questions/{qid}/toggle-explored")
def question_toggle(qid: str, redirect_to: str = Form("/")):
    if not db.get_question(qid):
        raise HTTPException(404)
    db.toggle_question_explored(qid)
    return RedirectResponse(url=redirect_to, status_code=303)


@app.get("/questions/{qid}")
def question_detail(request: Request, qid: str):
    q = db.get_question(qid)
    if not q:
        raise HTTPException(404)
    st = db.get_subtopic(q["subtopic_id"])
    if not st:
        raise HTTPException(404)
    cat = db.get_category(st["category_id"])
    res = db.get_resource(q["resource_id"]) if q.get("resource_id") else None
    return templates.TemplateResponse(
        request,
        "question_detail.html",
        {
            "title": "Question",
            "category": cat,
            "subtopic": st,
            "breadcrumb": db.subtopic_breadcrumb(st["id"]),
            "question": q,
            "resource": res,
        },
    )


@app.get("/writings/new")
def writing_new(request: Request, subtopic_id: str, resource_id: str = "", question_id: str = ""):
    st = db.get_subtopic(subtopic_id)
    if not st:
        raise HTTPException(404)
    cat = db.get_category(st["category_id"])
    res = db.get_resource(resource_id) if resource_id else None
    q = db.get_question(question_id) if question_id else None
    return templates.TemplateResponse(
        request,
        "writing_edit.html",
        {
            "title": "New writing",
            "category": cat,
            "subtopic": st,
            "breadcrumb": db.subtopic_breadcrumb(subtopic_id),
            "writing": None,
            "form_title": "",
            "form_body": "",
            "resource": res,
            "question": q,
            "resource_id": resource_id or "",
            "question_id": question_id or "",
        },
    )


@app.get("/writings/{wid}/edit")
def writing_edit(request: Request, wid: str):
    w = db.get_writing(wid)
    if not w:
        raise HTTPException(404)
    st = db.get_subtopic(w["subtopic_id"])
    if not st:
        raise HTTPException(404)
    cat = db.get_category(st["category_id"])
    res = db.get_resource(w["resource_id"]) if w.get("resource_id") else None
    q = db.get_question(w["question_id"]) if w.get("question_id") else None
    return templates.TemplateResponse(
        request,
        "writing_edit.html",
        {
            "title": w.get("title") or "Writing",
            "category": cat,
            "subtopic": st,
            "breadcrumb": db.subtopic_breadcrumb(st["id"]),
            "writing": w,
            "form_title": w.get("title") or "",
            "form_body": w.get("body") or "",
            "resource": res,
            "question": q,
            "resource_id": w.get("resource_id") or "",
            "question_id": w.get("question_id") or "",
        },
    )


@app.post("/writings/save")
def writing_save(
    subtopic_id: str = Form(...),
    title: str = Form(""),
    body: str = Form(...),
    writing_id: str = Form(""),
    resource_id: str = Form(""),
    question_id: str = Form(""),
):
    if not db.get_subtopic(subtopic_id):
        raise HTTPException(404)
    rid = resource_id.strip() or None
    qid = question_id.strip() or None
    if writing_id.strip():
        db.update_writing(
            writing_id.strip(),
            title=title or None,
            body=body,
            resource_id=rid,
            question_id=qid,
            clear_refined=True,
        )
        return RedirectResponse(url=f"/writings/{writing_id.strip()}/edit", status_code=303)
    wid = db.create_writing(subtopic_id, body, title, rid, qid)
    return RedirectResponse(url=f"/writings/{wid}/edit", status_code=303)


@app.post("/writings/{wid}/refine")
def writing_refine(wid: str, body: str = Form(...), focus: str = Form("")):
    if not db.get_writing(wid):
        raise HTTPException(404)
    db.update_writing(wid, body=body, clear_refined=False)
    f = focus.strip() or "clarity, structure, and precision while preserving the author's voice"
    suggestion = research_ai.refine_writing(body, f)
    db.set_writing_refined_suggestion(wid, suggestion)
    return RedirectResponse(url=f"/writings/{wid}/edit", status_code=303)


@app.post("/writings/{wid}/apply-refined")
def writing_apply_refined(wid: str):
    if not db.get_writing(wid):
        raise HTTPException(404)
    db.apply_writing_refined(wid)
    return RedirectResponse(url=f"/writings/{wid}/edit", status_code=303)


@app.post("/writings/{wid}/discard-refined")
def writing_discard_refined(wid: str):
    if not db.get_writing(wid):
        raise HTTPException(404)
    db.set_writing_refined_suggestion(wid, None)
    return RedirectResponse(url=f"/writings/{wid}/edit", status_code=303)


@app.get("/files/resource/{rid}")
def download_resource_file(rid: str):
    r = db.get_resource(rid)
    if not r or not r.get("file_path"):
        raise HTTPException(404)
    fp = os.path.normpath(os.path.join(BASE_DIR, r["file_path"]))
    if not fp.startswith(os.path.normpath(BASE_DIR)):
        raise HTTPException(400)
    if not os.path.isfile(fp):
        raise HTTPException(404)
    return FileResponse(fp, filename=os.path.basename(fp))
