"""
Local research workspace — FastAPI + Jinja2 + SQLite.
Run from repo root: python -m uvicorn app.workspace_app:app --reload --port 8765
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import research_db as db
from app import research_ai
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


def _extract_uploaded_text(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return extract_text_from_pdf(path) or ""
    if ext == ".txt":
        return extract_text_from_txt(path) or ""
    if ext == ".docx":
        return extract_text_from_docx(path) or ""
    return ""


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
        },
    )


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
    st = db.get_subtopic(subtopic_id)
    if not st:
        raise HTTPException(404)
    rid = db.create_resource(subtopic_id, title or "Untitled resource", source_type, None, summary, notes)
    if file and file.filename:
        raw_name = os.path.basename(file.filename)
        ext = os.path.splitext(raw_name)[1].lower()
        stored = f"{rid}{ext}" if ext else rid
        dest = os.path.join(db.UPLOADS_DIR, stored)
        os.makedirs(db.UPLOADS_DIR, exist_ok=True)
        content = await file.read()
        with open(dest, "wb") as f:
            f.write(content)
        db.set_resource_file_path(rid, os.path.relpath(dest, BASE_DIR))
        text = _extract_uploaded_text(dest)
        if text.strip() and (not summary.strip() or not title.strip()):
            meta = research_ai.summarize_for_resource(text)
            new_title = title.strip() or meta["title"]
            new_summary = summary.strip() or meta["summary"]
            db.update_resource(rid, title=new_title, summary=new_summary)
    return RedirectResponse(url=f"/resources/{rid}", status_code=303)


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
            extra = _extract_uploaded_text(fp)[:4000]
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
