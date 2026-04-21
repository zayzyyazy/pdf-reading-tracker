"""
SQLite persistence for the research workspace (categories → subtopics → resources, questions, writings).
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "research.sqlite")
UPLOADS_DIR = os.path.join(BASE_DIR, "data", "workspace_uploads")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return str(uuid.uuid4())


@contextmanager
def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    with connect() as conn:
        conn.executescript(
            """
            PRAGMA foreign_keys = ON;

            CREATE TABLE IF NOT EXISTS categories (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS subtopics (
                id TEXT PRIMARY KEY,
                category_id TEXT NOT NULL,
                parent_id TEXT,
                name TEXT NOT NULL,
                research_field TEXT,
                topic_summary TEXT,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE CASCADE,
                FOREIGN KEY (parent_id) REFERENCES subtopics(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS resources (
                id TEXT PRIMARY KEY,
                subtopic_id TEXT NOT NULL,
                title TEXT NOT NULL,
                source_type TEXT,
                file_path TEXT,
                summary TEXT,
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (subtopic_id) REFERENCES subtopics(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS questions (
                id TEXT PRIMARY KEY,
                subtopic_id TEXT NOT NULL,
                resource_id TEXT,
                body TEXT NOT NULL,
                is_main INTEGER NOT NULL DEFAULT 0,
                explored INTEGER NOT NULL DEFAULT 0,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY (subtopic_id) REFERENCES subtopics(id) ON DELETE CASCADE,
                FOREIGN KEY (resource_id) REFERENCES resources(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS writings (
                id TEXT PRIMARY KEY,
                subtopic_id TEXT NOT NULL,
                title TEXT,
                body TEXT NOT NULL,
                resource_id TEXT,
                question_id TEXT,
                refined_suggestion TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (subtopic_id) REFERENCES subtopics(id) ON DELETE CASCADE,
                FOREIGN KEY (resource_id) REFERENCES resources(id) ON DELETE SET NULL,
                FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_subtopics_category ON subtopics(category_id);
            CREATE INDEX IF NOT EXISTS idx_subtopics_parent ON subtopics(parent_id);
            CREATE INDEX IF NOT EXISTS idx_resources_subtopic ON resources(subtopic_id);
            CREATE INDEX IF NOT EXISTS idx_questions_subtopic ON questions(subtopic_id);
            CREATE INDEX IF NOT EXISTS idx_questions_resource ON questions(resource_id);
            CREATE INDEX IF NOT EXISTS idx_writings_subtopic ON writings(subtopic_id);
            """
        )
        _ensure_writings_refined_column(conn)
    seed_if_empty()


def _ensure_writings_refined_column(conn: sqlite3.Connection) -> None:
    cur = conn.execute("PRAGMA table_info(writings)")
    cols = {row[1] for row in cur.fetchall()}
    if "refined_suggestion" not in cols:
        conn.execute("ALTER TABLE writings ADD COLUMN refined_suggestion TEXT")


def seed_if_empty() -> None:
    with connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
        if n > 0:
            return
        ts = _now()
        cat_ai = _uid()
        cat_phil = _uid()
        conn.execute(
            "INSERT INTO categories (id, name, description, sort_order, created_at) VALUES (?,?,?,?,?)",
            (cat_ai, "Artificial intelligence", "Models, alignment, and systems I want to understand deeply.", 0, ts),
        )
        conn.execute(
            "INSERT INTO categories (id, name, description, sort_order, created_at) VALUES (?,?,?,?,?)",
            (cat_phil, "Philosophy of mind", "Consciousness, representation, and how we think about thinking.", 1, ts),
        )
        st_eval = _uid()
        st_interp = _uid()
        conn.execute(
            """INSERT INTO subtopics
            (id, category_id, parent_id, name, research_field, topic_summary, sort_order, created_at)
            VALUES (?,?,?,?,?,?,?,?)""",
            (
                st_eval,
                cat_ai,
                None,
                "Evaluation & benchmarks",
                "ML evaluation; psychometrics of models",
                "What we measure when we say a model is 'good', and what we miss.",
                0,
                ts,
            ),
        )
        conn.execute(
            """INSERT INTO subtopics
            (id, category_id, parent_id, name, research_field, topic_summary, sort_order, created_at)
            VALUES (?,?,?,?,?,?,?,?)""",
            (
                st_interp,
                cat_phil,
                None,
                "Interpretability and understanding",
                "Philosophy of science; XAI",
                "Whether 'understanding' a network is like understanding a theory or a person.",
                0,
                ts,
            ),
        )
        res_id = _uid()
        conn.execute(
            """INSERT INTO resources
            (id, subtopic_id, title, source_type, file_path, summary, notes, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                res_id,
                st_eval,
                "Example reading: what benchmarks actually test",
                "article",
                None,
                "Placeholder resource — add a PDF or paste a summary after you upload your own file.",
                "Use this row to try questions and writing; delete it anytime.",
                ts,
                ts,
            ),
        )
        conn.execute(
            """INSERT INTO questions
            (id, subtopic_id, resource_id, body, is_main, explored, sort_order, created_at)
            VALUES (?,?,?,?,?,?,?,?)""",
            (_uid(), st_eval, res_id, "If the benchmark shifts, does the capability we care about shift with it?", 1, 0, 0, ts),
        )
        conn.execute(
            """INSERT INTO questions
            (id, subtopic_id, resource_id, body, is_main, explored, sort_order, created_at)
            VALUES (?,?,?,?,?,?,?,?)""",
            (_uid(), st_eval, None, "What cluster of failure modes should evaluation prioritize for my own work?", 0, 0, 1, ts),
        )
        conn.execute(
            """INSERT INTO writings
            (id, subtopic_id, title, body, resource_id, question_id, refined_suggestion, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                _uid(),
                st_eval,
                "Rough note: what I want from evaluation",
                "I still care less about leaderboard scores than about whether the system fails gracefully in the situations I actually deploy in.",
                res_id,
                None,
                None,
                ts,
                ts,
            ),
        )


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


# --- Categories ---


def list_categories() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM categories ORDER BY sort_order ASC, name ASC"
        ).fetchall()
        return [row_to_dict(r) for r in rows]


def get_category(cat_id: str) -> Optional[dict[str, Any]]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM categories WHERE id = ?", (cat_id,)).fetchone()
        return row_to_dict(row) if row else None


def create_category(name: str, description: str = "") -> str:
    cid = _uid()
    ts = _now()
    mx = 0
    with connect() as conn:
        r = conn.execute("SELECT COALESCE(MAX(sort_order), -1) FROM categories").fetchone()
        if r:
            mx = int(r[0]) + 1
        conn.execute(
            "INSERT INTO categories (id, name, description, sort_order, created_at) VALUES (?,?,?,?,?)",
            (cid, name.strip(), description.strip() or None, mx, ts),
        )
    return cid


def category_counts(cat_id: str) -> dict[str, int]:
    with connect() as conn:
        subtopics = conn.execute(
            "SELECT COUNT(*) FROM subtopics WHERE category_id = ?", (cat_id,)
        ).fetchone()[0]
        resources = conn.execute(
            """SELECT COUNT(*) FROM resources r
            JOIN subtopics s ON s.id = r.subtopic_id WHERE s.category_id = ?""",
            (cat_id,),
        ).fetchone()[0]
        questions = conn.execute(
            """SELECT COUNT(*) FROM questions q
            JOIN subtopics s ON s.id = q.subtopic_id WHERE s.category_id = ?""",
            (cat_id,),
        ).fetchone()[0]
        writings = conn.execute(
            """SELECT COUNT(*) FROM writings w
            JOIN subtopics s ON s.id = w.subtopic_id WHERE s.category_id = ?""",
            (cat_id,),
        ).fetchone()[0]
        open_q = conn.execute(
            """SELECT COUNT(*) FROM questions q
            JOIN subtopics s ON s.id = q.subtopic_id
            WHERE s.category_id = ? AND q.explored = 0""",
            (cat_id,),
        ).fetchone()[0]
    return {
        "subtopics": int(subtopics),
        "resources": int(resources),
        "questions": int(questions),
        "writings": int(writings),
        "open_questions": int(open_q),
    }


# --- Subtopics ---


def list_subtopics_for_category(cat_id: str, parent_id: Optional[str] = None) -> list[dict[str, Any]]:
    with connect() as conn:
        if parent_id is None:
            rows = conn.execute(
                """SELECT * FROM subtopics
                WHERE category_id = ? AND parent_id IS NULL
                ORDER BY sort_order ASC, name ASC""",
                (cat_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM subtopics
                WHERE category_id = ? AND parent_id = ?
                ORDER BY sort_order ASC, name ASC""",
                (cat_id, parent_id),
            ).fetchall()
        return [row_to_dict(r) for r in rows]


def get_subtopic(sid: str) -> Optional[dict[str, Any]]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM subtopics WHERE id = ?", (sid,)).fetchone()
        return row_to_dict(row) if row else None


def create_subtopic(
    category_id: str,
    name: str,
    parent_id: Optional[str] = None,
    research_field: str = "",
    topic_summary: str = "",
) -> str:
    sid = _uid()
    ts = _now()
    with connect() as conn:
        r = conn.execute(
            """SELECT COALESCE(MAX(sort_order), -1) FROM subtopics
            WHERE category_id = ? AND ((parent_id IS NULL AND ? IS NULL) OR parent_id = ?)""",
            (category_id, parent_id, parent_id),
        ).fetchone()
        mx = int(r[0]) + 1 if r else 0
        conn.execute(
            """INSERT INTO subtopics
            (id, category_id, parent_id, name, research_field, topic_summary, sort_order, created_at)
            VALUES (?,?,?,?,?,?,?,?)""",
            (
                sid,
                category_id,
                parent_id,
                name.strip(),
                research_field.strip() or None,
                topic_summary.strip() or None,
                mx,
                ts,
            ),
        )
    return sid


def subtopic_counts(sid: str) -> dict[str, int]:
    with connect() as conn:
        children = conn.execute(
            "SELECT COUNT(*) FROM subtopics WHERE parent_id = ?", (sid,)
        ).fetchone()[0]
        resources = conn.execute(
            "SELECT COUNT(*) FROM resources WHERE subtopic_id = ?", (sid,)
        ).fetchone()[0]
        questions = conn.execute(
            "SELECT COUNT(*) FROM questions WHERE subtopic_id = ?", (sid,)
        ).fetchone()[0]
        writings = conn.execute(
            "SELECT COUNT(*) FROM writings WHERE subtopic_id = ?", (sid,)
        ).fetchone()[0]
    return {
        "children": int(children),
        "resources": int(resources),
        "questions": int(questions),
        "writings": int(writings),
    }


def list_child_subtopics(parent_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM subtopics WHERE parent_id = ? ORDER BY sort_order ASC, name ASC",
            (parent_id,),
        ).fetchall()
        return [row_to_dict(r) for r in rows]


# --- Resources ---


def list_resources_for_subtopic(sid: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM resources WHERE subtopic_id = ? ORDER BY updated_at DESC",
            (sid,),
        ).fetchall()
        return [row_to_dict(r) for r in rows]


def get_resource(rid: str) -> Optional[dict[str, Any]]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM resources WHERE id = ?", (rid,)).fetchone()
        return row_to_dict(row) if row else None


def create_resource(
    subtopic_id: str,
    title: str,
    source_type: str = "pdf",
    file_path: Optional[str] = None,
    summary: str = "",
    notes: str = "",
) -> str:
    rid = _uid()
    ts = _now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO resources
            (id, subtopic_id, title, source_type, file_path, summary, notes, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (rid, subtopic_id, title.strip(), source_type.strip() or "other", file_path, summary or None, notes or None, ts, ts),
        )
    return rid


def update_resource(
    rid: str,
    title: Optional[str] = None,
    source_type: Optional[str] = None,
    summary: Optional[str] = None,
    notes: Optional[str] = None,
) -> None:
    ts = _now()
    cur = get_resource(rid)
    if not cur:
        return
    with connect() as conn:
        conn.execute(
            """UPDATE resources SET
            title = COALESCE(?, title),
            source_type = COALESCE(?, source_type),
            summary = COALESCE(?, summary),
            notes = COALESCE(?, notes),
            updated_at = ?
            WHERE id = ?""",
            (
                title,
                source_type,
                summary,
                notes,
                ts,
                rid,
            ),
        )


def set_resource_file_path(rid: str, file_path: str) -> None:
    ts = _now()
    with connect() as conn:
        conn.execute(
            "UPDATE resources SET file_path = ?, updated_at = ? WHERE id = ?",
            (file_path, ts, rid),
        )


# --- Questions ---


def list_questions_for_subtopic(sid: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT * FROM questions WHERE subtopic_id = ?
            ORDER BY is_main DESC, sort_order ASC, created_at DESC""",
            (sid,),
        ).fetchall()
        return [row_to_dict(r) for r in rows]


def list_questions_for_resource(rid: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM questions WHERE resource_id = ? ORDER BY is_main DESC, sort_order ASC",
            (rid,),
        ).fetchall()
        return [row_to_dict(r) for r in rows]


def create_question(
    subtopic_id: str,
    body: str,
    resource_id: Optional[str] = None,
    is_main: bool = False,
) -> str:
    qid = _uid()
    ts = _now()
    with connect() as conn:
        r = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) FROM questions WHERE subtopic_id = ?",
            (subtopic_id,),
        ).fetchone()
        mx = int(r[0]) + 1 if r else 0
        conn.execute(
            """INSERT INTO questions
            (id, subtopic_id, resource_id, body, is_main, explored, sort_order, created_at)
            VALUES (?,?,?,?,?,?,?,?)""",
            (qid, subtopic_id, resource_id, body.strip(), 1 if is_main else 0, 0, mx, ts),
        )
    return qid


def get_question(qid: str) -> Optional[dict[str, Any]]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM questions WHERE id = ?", (qid,)).fetchone()
        return row_to_dict(row) if row else None


def toggle_question_explored(qid: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE questions SET explored = CASE explored WHEN 1 THEN 0 ELSE 1 END WHERE id = ?",
            (qid,),
        )


def insert_questions_bulk(subtopic_id: str, resource_id: Optional[str], items: list[str]) -> int:
    ts = _now()
    n = 0
    with connect() as conn:
        base = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) FROM questions WHERE subtopic_id = ?",
            (subtopic_id,),
        ).fetchone()
        mx = int(base[0]) + 1 if base else 0
        for i, body in enumerate(items):
            if not body.strip():
                continue
            conn.execute(
                """INSERT INTO questions
                (id, subtopic_id, resource_id, body, is_main, explored, sort_order, created_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (_uid(), subtopic_id, resource_id, body.strip(), 0, 0, mx + i, ts),
            )
            n += 1
    return n


# --- Writings ---


def list_writings_for_subtopic(sid: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM writings WHERE subtopic_id = ? ORDER BY updated_at DESC",
            (sid,),
        ).fetchall()
        return [row_to_dict(r) for r in rows]


def get_writing(wid: str) -> Optional[dict[str, Any]]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM writings WHERE id = ?", (wid,)).fetchone()
        return row_to_dict(row) if row else None


def create_writing(
    subtopic_id: str,
    body: str,
    title: str = "",
    resource_id: Optional[str] = None,
    question_id: Optional[str] = None,
) -> str:
    wid = _uid()
    ts = _now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO writings
            (id, subtopic_id, title, body, resource_id, question_id, refined_suggestion, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                wid,
                subtopic_id,
                title.strip() or None,
                body,
                resource_id,
                question_id,
                None,
                ts,
                ts,
            ),
        )
    return wid


_MISSING = object()


def update_writing(
    wid: str,
    title: Optional[str] = None,
    body: Optional[str] = None,
    resource_id: Any = _MISSING,
    question_id: Any = _MISSING,
    clear_refined: bool = False,
) -> None:
    ts = _now()
    w = get_writing(wid)
    if not w:
        return
    refined = None if clear_refined else w.get("refined_suggestion")
    new_rid = w["resource_id"] if resource_id is _MISSING else resource_id
    new_qid = w["question_id"] if question_id is _MISSING else question_id
    with connect() as conn:
        conn.execute(
            """UPDATE writings SET
            title = COALESCE(?, title),
            body = COALESCE(?, body),
            resource_id = ?,
            question_id = ?,
            refined_suggestion = ?,
            updated_at = ?
            WHERE id = ?""",
            (
                title,
                body,
                new_rid,
                new_qid,
                refined,
                ts,
                wid,
            ),
        )


def set_writing_refined_suggestion(wid: str, text: Optional[str]) -> None:
    ts = _now()
    with connect() as conn:
        conn.execute(
            "UPDATE writings SET refined_suggestion = ?, updated_at = ? WHERE id = ?",
            (text, ts, wid),
        )


def apply_writing_refined(wid: str) -> None:
    w = get_writing(wid)
    if not w or not w.get("refined_suggestion"):
        return
    ts = _now()
    with connect() as conn:
        conn.execute(
            "UPDATE writings SET body = ?, refined_suggestion = NULL, updated_at = ? WHERE id = ?",
            (w["refined_suggestion"], ts, wid),
        )


# --- Dashboard helpers ---


def recent_resources(limit: int = 8) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT r.*, s.name AS subtopic_name, s.category_id,
            c.name AS category_name
            FROM resources r
            JOIN subtopics s ON s.id = r.subtopic_id
            JOIN categories c ON c.id = s.category_id
            ORDER BY r.updated_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [row_to_dict(r) for r in rows]


def recent_writings(limit: int = 6) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT w.*, s.name AS subtopic_name, c.name AS category_name
            FROM writings w
            JOIN subtopics s ON s.id = w.subtopic_id
            JOIN categories c ON c.id = s.category_id
            ORDER BY w.updated_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [row_to_dict(r) for r in rows]


def open_questions_globally(limit: int = 12) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT q.*, s.name AS subtopic_name, c.name AS category_name
            FROM questions q
            JOIN subtopics s ON s.id = q.subtopic_id
            JOIN categories c ON c.id = s.category_id
            WHERE q.explored = 0
            ORDER BY q.created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [row_to_dict(r) for r in rows]


def subtopic_breadcrumb(sid: str) -> list[dict[str, Any]]:
    """Ancestors from root to current (inclusive), each {id, name, category_id}."""
    chain: list[dict[str, Any]] = []
    cur_id: Optional[str] = sid
    with connect() as conn:
        while cur_id:
            row = conn.execute("SELECT id, name, parent_id, category_id FROM subtopics WHERE id = ?", (cur_id,)).fetchone()
            if not row:
                break
            chain.append({"id": row["id"], "name": row["name"], "category_id": row["category_id"]})
            cur_id = row["parent_id"]
    chain.reverse()
    return chain


def delete_resource(rid: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM resources WHERE id = ?", (rid,))
