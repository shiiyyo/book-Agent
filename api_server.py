# -*- coding: utf-8 -*-
"""
极简后端：只提供 API 给前端调用；生成任务在后台线程执行，支持实时取消与微调（编辑保存、修改说明）。
启动：在项目根目录执行  py api_server.py  ，浏览器打开  http://127.0.0.1:5000
"""
from __future__ import annotations

import io
import json
import re
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from flask import Flask, jsonify, request, send_file, send_from_directory
from sqlalchemy import delete, select, text

from app.config import get_default_model
from core.engine import run_task, _build_outline_content, _parse_outline_to_intro_and_fragments
from database import (
    Argument,
    Book,
    Chapter,
    Citation,
    Conflict,
    GenerationTask,
    Outline,
    Reference,
    TaskStatus,
    TaskType,
    Term,
    get_session,
    init_db,
)
from database.connection import ensure_content_type_column


def _run_task_in_background(book_id: int, task_id: int) -> None:
    """在后台线程中执行单条生成任务，便于前端轮询与取消。"""
    session = get_session()
    try:
        task = session.get(GenerationTask, task_id)
        if not task or task.book_id != book_id or task.status != TaskStatus.PENDING:
            return
        task.status = TaskStatus.RUNNING
        task.progress_message = "运行中…"
        session.commit()
        session.refresh(task)
        glossary = _load_glossary(session, book_id) if task.task_type in (TaskType.CHAPTER, TaskType.REWRITE) else None
        run_task(session, task, glossary_terms=glossary)
        session.refresh(task)
        if task.status != TaskStatus.CANCELLED:
            session.commit()
        else:
            session.rollback()
    except Exception as e:
        session.rollback()
        try:
            task = session.get(GenerationTask, task_id)
            if task:
                task.status = TaskStatus.FAILED
                task.error_message = (str(e) or str(sys.exc_info()[1]))[:2000]
                session.commit()
        except Exception:
            pass
    finally:
        session.close()

app = Flask(__name__, static_folder=str(ROOT / "static"), static_url_path="")
app.config["JSON_AS_ASCII"] = False


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# ---------- API ----------

@app.route("/api/config", methods=["GET"])
def get_config():
    """返回当前配置（如调用模型来自 .env），供前端展示。"""
    return jsonify({"default_model": get_default_model()})


@app.route("/api/books", methods=["GET"])
def list_books():
    session = get_session()
    try:
        books = session.execute(select(Book).order_by(Book.updated_at.desc())).scalars().all()
        return jsonify([{"id": b.id, "title": b.title, "core_concept": b.core_concept, "default_model": b.default_model or "", "content_type": getattr(b, "content_type", None) or "academic"} for b in books])
    finally:
        session.close()


@app.route("/api/books", methods=["POST"])
def create_book():
    data = request.get_json() or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "书名为空"}), 400
    content_type = (data.get("content_type") or "academic").strip().lower()
    if content_type not in ("academic", "novel"):
        content_type = "academic"
    session = get_session()
    try:
        default_model = (data.get("default_model") or get_default_model() or "").strip() or None
        book = Book(title=title, core_concept=(data.get("core_concept") or "").strip() or None, default_model=default_model, content_type=content_type, book_type=content_type)
        session.add(book)
        session.commit()
        session.refresh(book)
        return jsonify({"id": book.id, "title": book.title, "content_type": getattr(book, "content_type", None) or "academic"})
    finally:
        session.close()


@app.route("/api/books/<int:bid>/export", methods=["GET"])
def export_book(bid):
    """导出书籍为 Markdown 或 Word。?format=md 或 format=docx（须在 get_book 之前注册以免被覆盖）"""
    fmt = (request.args.get("format") or "md").strip().lower()
    if fmt not in ("md", "docx"):
        return jsonify({"error": "仅支持 format=md 或 format=docx"}), 400
    session = get_session()
    try:
        book = session.get(Book, bid)
        if not book:
            return jsonify({"error": "书籍不存在"}), 404
        outline = book.outline
        chapters = sorted(book.chapters, key=lambda c: c.order_index)
        base_name = _sanitize_filename(book.title or "book")
        if fmt == "md":
            content = _book_to_markdown(book, outline, chapters)
            buf = io.BytesIO(content.encode("utf-8"))
            return send_file(
                buf,
                mimetype="text/markdown; charset=utf-8",
                as_attachment=True,
                download_name=base_name + ".md",
            )
        else:
            try:
                raw = _book_to_docx(book, outline, chapters)
            except ImportError:
                return jsonify({"error": "请安装 python-docx: pip install python-docx"}), 500
            return send_file(
                io.BytesIO(raw),
                mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                as_attachment=True,
                download_name=base_name + ".docx",
            )
    finally:
        session.close()


@app.route("/api/books/<int:bid>", methods=["GET"])
def get_book(bid):
    session = get_session()
    try:
        book = session.get(Book, bid)
        if not book:
            return jsonify({"error": "书籍不存在"}), 404
        outline = None
        chapters = sorted(book.chapters, key=lambda c: c.order_index)
        if book.outline:
            content = book.outline.content or ""
            intro = getattr(book.outline, "intro", None) or ""
            if intro is not None and any(getattr(c, "outline_fragment", None) and (c.outline_fragment or "").strip() for c in chapters):
                content = _build_outline_content(intro, chapters) or content
            outline = {"content": content, "raw_json": book.outline.raw_json}
        terms = [{"term": t.term, "definition": t.definition or ""} for t in book.terms]
        return jsonify({
            "id": book.id,
            "title": book.title,
            "core_concept": book.core_concept,
            "default_model": book.default_model or "",
            "content_type": getattr(book, "content_type", None) or "academic",
            "preface": getattr(book, "preface", None) or "",
            "target_publisher": getattr(book, "target_publisher", None) or "",
            "writing_style": getattr(book, "writing_style", None) or "",
            "style_reference_text": getattr(book, "style_reference_text", None) or "",
            "academic_tone": getattr(book, "academic_tone", None) or "strict",
            "outline": outline,
            "chapters": [{"id": c.id, "order_index": c.order_index, "title": c.title, "outline_content": c.outline_content, "content": c.content, "outline_fragment": getattr(c, "outline_fragment", None) or ""} for c in chapters],
            "terms": terms,
        })
    finally:
        session.close()


@app.route("/api/books/<int:bid>/check", methods=["GET"])
def check_book_content_type(bid):
    """诊断接口：确保 content_type 列存在，并返回该书在数据库中的 content_type 实际值。"""
    ensure_content_type_column()
    session = get_session()
    try:
        book = session.get(Book, bid)
        if not book:
            return jsonify({"error": "书籍不存在"}), 404
        orm_value = getattr(book, "content_type", None)
        raw_value = None
        try:
            from database.connection import engine
            with engine.connect() as conn:
                row = conn.execute(text("SELECT content_type FROM books WHERE id = :id"), {"id": bid}).fetchone()
                raw_value = row[0] if row else None
        except Exception as e:
            raw_value = f"(查询失败: {e})"
        return jsonify({
            "book_id": bid,
            "title": book.title,
            "content_type_orm": orm_value,
            "content_type_raw": raw_value,
            "hint": "若 content_type_raw 为 null 或 academic 但您选的是网络小说，请在前端把内容类型改为「网络小说」并保存后再生成。",
        })
    finally:
        session.close()


@app.route("/api/books/<int:bid>", methods=["PUT"])
def update_book(bid):
    data = request.get_json() or {}
    session = get_session()
    try:
        book = session.get(Book, bid)
        if not book:
            return jsonify({"error": "书籍不存在"}), 404
        if "default_model" in data:
            book.default_model = (data["default_model"] or "").strip() or book.default_model
        if "content_type" in data and hasattr(book, "content_type"):
            ct = (data["content_type"] or "").strip().lower()
            if ct in ("academic", "novel"):
                book.content_type = ct
                if hasattr(book, "book_type"):
                    book.book_type = ct
        if "preface" in data and hasattr(book, "preface"):
            if data["preface"] is not None:
                book.preface = _normalize_paragraph_spacing(data["preface"])
        if "target_publisher" in data and hasattr(book, "target_publisher"):
            book.target_publisher = (data["target_publisher"] or "").strip() or None
        if "writing_style" in data and hasattr(book, "writing_style"):
            book.writing_style = (data["writing_style"] or "").strip() or None
        if "style_reference_text" in data and hasattr(book, "style_reference_text"):
            book.style_reference_text = (data["style_reference_text"] or "").strip() or None
        if "academic_tone" in data and hasattr(book, "academic_tone"):
            t = (data["academic_tone"] or "").strip().lower()
            book.academic_tone = t if t in ("strict", "bestseller") else (book.academic_tone or "strict")
        session.commit()
        return jsonify({"ok": True})
    finally:
        session.close()


@app.route("/api/books/<int:bid>/outline", methods=["PUT"])
def update_outline(bid):
    """保存用户对大纲内容的手动修改（微调）；同步拆分为 intro + 各章 outline_fragment 便于局部修改。"""
    data = request.get_json() or {}
    content = (data.get("content") or "").strip()
    session = get_session()
    try:
        book = session.get(Book, bid)
        if not book:
            return jsonify({"error": "书籍不存在"}), 404
        content = _normalize_paragraph_spacing(content or "")
        if not book.outline:
            from database.models import Outline
            book.outline = Outline(book_id=bid, content=content or "", raw_json=None)
            session.add(book.outline)
        else:
            book.outline.content = content or book.outline.content or ""
        intro, fragments = _parse_outline_to_intro_and_fragments(content or "")
        if getattr(book.outline, "intro", None) is not None:
            book.outline.intro = intro
        chapters = sorted(book.chapters, key=lambda c: c.order_index)
        frag_by_idx = {idx: frag for idx, frag in fragments}
        for ch in chapters:
            if ch.order_index in frag_by_idx and hasattr(ch, "outline_fragment"):
                ch.outline_fragment = frag_by_idx[ch.order_index]
        session.commit()
        return jsonify({"ok": True})
    finally:
        session.close()


@app.route("/api/books/<int:bid>/chapters/<int:cid>", methods=["PUT"])
def update_chapter(bid, cid):
    """保存用户对章节正文的手动修改（微调）。"""
    data = request.get_json() or {}
    if "content" in data:
        session = get_session()
        try:
            book = session.get(Book, bid)
            ch = session.get(Chapter, cid)
            if not book or not ch or ch.book_id != bid:
                return jsonify({"error": "书籍或章节不存在"}), 404
            raw_content = data.get("content")
            if raw_content is not None:
                ch.content = _normalize_paragraph_spacing(raw_content)
            if ch.content:
                ch.word_count = len(ch.content)
            session.commit()
            return jsonify({"ok": True})
        finally:
            session.close()
    return jsonify({"error": "请提供 content 字段"}), 400


@app.route("/api/books/<int:bid>", methods=["DELETE"])
def delete_book(bid):
    session = get_session()
    try:
        book = session.get(Book, bid)
        if not book:
            return jsonify({"error": "书籍不存在"}), 404
        # 按依赖顺序删除关联数据，避免 SQLite 外键或 CASCADE 未生效时报错
        session.execute(delete(Conflict).where(Conflict.book_id == bid))
        session.execute(delete(GenerationTask).where(GenerationTask.book_id == bid))
        chapter_ids = [r[0] for r in session.execute(select(Chapter.id).where(Chapter.book_id == bid)).all()]
        if chapter_ids:
            session.execute(delete(Citation).where(Citation.chapter_id.in_(chapter_ids)))
        session.execute(delete(Chapter).where(Chapter.book_id == bid))
        session.execute(delete(Reference).where(Reference.book_id == bid))
        session.execute(delete(Argument).where(Argument.book_id == bid))
        session.execute(delete(Term).where(Term.book_id == bid))
        session.execute(delete(Outline).where(Outline.book_id == bid))
        session.delete(book)
        session.commit()
        return jsonify({"ok": True})
    except Exception as e:
        session.rollback()
        return jsonify({"error": "删除失败: " + str(e)}), 500
    finally:
        session.close()


def _sanitize_filename(name: str, max_len: int = 80) -> str:
    """将书名转为安全文件名：去掉非法字符并截断长度。"""
    if not name or not name.strip():
        return "book"
    s = re.sub(r'[\\/:*?"<>|]', "_", name.strip())
    return s[:max_len] if len(s) > max_len else s


def _book_to_markdown(book, outline, chapters) -> str:
    """将书籍内容拼接为纯文本导出（已去除 *、# 与多余空行，无 Markdown 符号）。"""
    def plain(s):
        return _export_plain(s) if s else ""
    lines = [plain(book.title or "未命名"), ""]
    if book.core_concept:
        lines.append("核心构思\n\n" + plain(book.core_concept or "") + "\n\n---\n")
    preface = getattr(book, "preface", None) or ""
    if preface and preface.strip():
        lines.append("前言\n\n" + plain(preface) + "\n\n---\n")
    if outline and outline.content:
        lines.append("全书大纲\n\n" + plain(outline.content or "") + "\n\n---\n")
    for ch in chapters:
        lines.append("第 {} 章 {}".format(ch.order_index, plain(ch.title or "")))
        if ch.outline_content and (ch.outline_content or "").strip():
            lines.append("\n本章要点： " + plain(ch.outline_content or "") + "\n")
        if ch.content and (ch.content or "").strip():
            lines.append("\n" + plain(ch.content or ""))
        else:
            lines.append("\n（本章正文尚未生成）")
        lines.append("\n")
    return "\n".join(lines)


def _normalize_paragraph_spacing(text: str | None) -> str:
    """标准化段落格式：去除 * 号、统一换行、多处空行合并为一段一空行。"""
    if not text or not isinstance(text, str):
        return (text or "").strip()
    import re
    s = text.replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("**", "").replace("*", "")
    s = re.sub(r"\n[\s]*\n[\s]*\n+", "\n\n", s)
    s = re.sub(r"\n{2,}", "\n\n", s)
    return s.strip()


def _strip_heading_hashes(text: str | None) -> str:
    """去除每行行首的 Markdown 标题符 #（一个或多个 # 及紧随其后的空白）。"""
    if not text or not isinstance(text, str):
        return (text or "").strip()
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = [re.sub(r"^#+\s*", "", line) for line in lines]
    return "\n".join(out).strip()


def _export_plain(text: str | None) -> str:
    """导出用：去除 *、# 与多余空行，得到纯文本。"""
    if not text or not isinstance(text, str):
        return (text or "").strip()
    s = _normalize_paragraph_spacing(text)
    s = _strip_heading_hashes(s)
    return s.strip()


def _strip_markdown_asterisks(text: str) -> str:
    """去除 Markdown 粗体/斜体标记（*、**），导出 Word 时再次确保无星号。"""
    if not text:
        return text
    s = text.replace("**", "")
    s = s.replace("*", "")
    return s


# Word 导出排版规范（与 SPEC.md §7、前端展示一致）：
# 正文：宋体小四、1.5 倍行距、首行缩进 2 字符、两端对齐。
# 一级「第一章」：黑体三号居中。
# 二级「一、」：黑体四号左对齐。
# 三级「（一）」：楷体小四左对齐。
# 强调仅加粗；导出即可直接用于交稿排版。


def _book_to_docx(book, outline, chapters) -> bytes:
    """将书籍内容生成为 Word 文档，遵循统一交稿排版规范（见本函数上方注释与 SPEC.md §7）。"""
    from docx import Document
    from docx.shared import Pt, Cm
    from docx.enum.text import WD_PARAGRAPH_ALIGNMENT

    def plain(s):
        return _export_plain(s) if s else ""

    doc = Document()
    # 正文：宋体小四、1.5 倍行距、首行缩进 2 字符、两端对齐
    FONT_SONG = "宋体"
    FONT_HEI = "黑体"
    FONT_KAI = "楷体"
    SIZE_XIAOSI = Pt(12)   # 小四
    SIZE_SIHAO = Pt(14)    # 四号
    SIZE_SANHAO = Pt(16)   # 三号
    INDENT_2CH = Cm(0.74)  # 首行缩进约 2 字符

    def set_body_style(p):
        p.paragraph_format.line_spacing = 1.5
        p.paragraph_format.first_line_indent = INDENT_2CH
        p.paragraph_format.alignment = WD_PARAGRAPH_ALIGNMENT.JUSTIFY
        for r in p.runs:
            r.font.name = FONT_SONG
            r.font.size = SIZE_XIAOSI
            r.font.bold = False
            r.font.italic = False

    def add_body(doc, text, bold_prefix=None):
        if not text and not bold_prefix:
            p = doc.add_paragraph()
            p.paragraph_format.line_spacing = 1.5
            p.paragraph_format.first_line_indent = INDENT_2CH
            p.paragraph_format.alignment = WD_PARAGRAPH_ALIGNMENT.JUSTIFY
            return
        p = doc.add_paragraph()
        p.paragraph_format.line_spacing = 1.5
        p.paragraph_format.first_line_indent = INDENT_2CH
        p.paragraph_format.alignment = WD_PARAGRAPH_ALIGNMENT.JUSTIFY
        if bold_prefix:
            r1 = p.add_run(bold_prefix)
            r1.font.name, r1.font.size = FONT_SONG, SIZE_XIAOSI
            r1.font.bold = True
            r2 = p.add_run(text)
            r2.font.name, r2.font.size = FONT_SONG, SIZE_XIAOSI
        else:
            r = p.add_run(text)
            r.font.name, r.font.size = FONT_SONG, SIZE_XIAOSI

    # 一级「第一章」：黑体三号居中
    def add_heading1(doc, text):
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(12)
        p.paragraph_format.space_after = Pt(6)
        p.paragraph_format.first_line_indent = Pt(0)
        p.paragraph_format.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
        r = p.add_run(text)
        r.font.name, r.font.size = FONT_HEI, SIZE_SANHAO
        r.font.bold = True

    # 二级「一、」：黑体四号左对齐
    def add_heading2(doc, text):
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(6)
        p.paragraph_format.space_after = Pt(3)
        p.paragraph_format.first_line_indent = Pt(0)
        p.paragraph_format.alignment = WD_PARAGRAPH_ALIGNMENT.LEFT
        r = p.add_run(text)
        r.font.name, r.font.size = FONT_HEI, SIZE_SIHAO
        r.font.bold = True

    # 三级「（一）」：楷体小四左对齐
    def add_heading3(doc, text):
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(3)
        p.paragraph_format.space_after = Pt(3)
        p.paragraph_format.first_line_indent = Pt(0)
        p.paragraph_format.alignment = WD_PARAGRAPH_ALIGNMENT.LEFT
        r = p.add_run(text)
        r.font.name, r.font.size = FONT_KAI, SIZE_XIAOSI
        r.font.bold = False

    def add_chapter_line(doc, line):
        line = (line or "").strip()
        if not line:
            add_body(doc, "")
            return
        if re.match(r"^第[一二三四五六七八九十\d]+章\s*", line) or re.match(r"^第\s*\d+\s*章\s*", line):
            add_heading1(doc, line)
        elif re.match(r"^[一二三四五六七八九十]+[、．.]\s*", line) or re.match(r"^\d+[、．.]\s*", line):
            add_heading2(doc, line)
        elif re.match(r"^[（(][一二三四五六七八九十]+[)）]\s*", line) or re.match(r"^[（(]\d+[)）]\s*", line):
            add_heading3(doc, line)
        else:
            add_body(doc, line)

    add_heading1(doc, plain(book.title or "未命名"))
    doc.add_paragraph()
    if book.core_concept and (book.core_concept or "").strip():
        add_body(doc, plain(book.core_concept or ""), bold_prefix="核心构思：")
        doc.add_paragraph()
    preface = getattr(book, "preface", None) or ""
    if preface and preface.strip():
        add_heading1(doc, "前言")
        for line in plain(preface).splitlines():
            add_body(doc, (line or "").strip())
        doc.add_paragraph()
    if outline and outline.content and (outline.content or "").strip():
        add_heading1(doc, "全书大纲")
        for line in plain(outline.content or "").splitlines():
            add_body(doc, (line or "").strip())
        doc.add_paragraph()
    for ch in chapters:
        add_heading1(doc, "第 {} 章 {}".format(ch.order_index, plain(ch.title or "")))
        if ch.outline_content and (ch.outline_content or "").strip():
            add_body(doc, plain(ch.outline_content or ""), bold_prefix="本章要点：")
        if ch.content and (ch.content or "").strip():
            for line in plain(ch.content or "").splitlines():
                add_chapter_line(doc, line)
        else:
            add_body(doc, "（本章正文尚未生成）")
        doc.add_paragraph()
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.read()


@app.route("/api/books/<int:bid>/generate-outline", methods=["POST"])
def generate_outline(bid):
    data = request.get_json() or {}
    revision_instruction = (data.get("revision_instruction") or "").strip() or None
    partial_revision = data.get("partial_revision") is True
    session = get_session()
    try:
        book = session.get(Book, bid)
        if not book:
            return jsonify({"error": "书籍不存在"}), 404
        if partial_revision:
            if not revision_instruction:
                return jsonify({"error": "仅局部修改时请填写修改意图"}), 400
            if not book.outline or not (book.outline.content or "").strip():
                return jsonify({"error": "仅局部修改需要先有全书大纲，请先生成大纲后再试"}), 400
        # 优先用请求体中的 content_type（与当前下拉框一致），避免与 DB 不同步导致仍走学术逻辑
        ct = (data.get("content_type") or getattr(book, "content_type", None) or "").strip().lower() or "academic"
        if ct not in ("academic", "novel"):
            ct = "academic"
        if hasattr(book, "content_type"):
            book.content_type = ct
        if hasattr(book, "book_type"):
            book.book_type = ct
        params = {"content_type": ct}
        if revision_instruction:
            params["revision_instruction"] = revision_instruction
        if partial_revision:
            params["partial_revision"] = True
        print("[generate-outline] book_id=%s content_type=%s (from_request=%s)" % (bid, ct, data.get("content_type")))
        task = GenerationTask(book_id=bid, task_type=TaskType.OUTLINE_LEVEL1, status=TaskStatus.PENDING, params=params)
        session.add(task)
        session.commit()
        session.refresh(task)
        tid = task.id
    except Exception as e:
        session.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        session.close()
    threading.Thread(target=_run_task_in_background, args=(bid, tid), daemon=True).start()
    return jsonify({"ok": True, "task_id": tid}), 202


@app.route("/api/books/<int:bid>/generate-preface", methods=["GET", "POST", "OPTIONS"], strict_slashes=False)
@app.route("/api/books/<int:bid>/generate-preface/", methods=["GET", "POST", "OPTIONS"], strict_slashes=False)
def generate_preface(bid):
    """生成前言（约 3000 字），后台任务。仅接受 POST；GET 返回 JSON 说明避免浏览器显示 HTML 405。"""
    if request.method == "OPTIONS":
        return "", 200, {"Allow": "POST", "Access-Control-Allow-Methods": "POST, OPTIONS"}
    if request.method != "POST":
        return jsonify({"error": "此接口仅支持 POST，请通过页面「生成前言」按钮操作"}), 405, {"Allow": "POST", "Content-Type": "application/json"}
    data = request.get_json() or {}
    session = get_session()
    try:
        book = session.get(Book, bid)
        if not book:
            return jsonify({"error": "书籍不存在"}), 404
        ct = (data.get("content_type") or getattr(book, "content_type", None) or "").strip().lower() or "academic"
        if ct not in ("academic", "novel"):
            ct = "academic"
        if hasattr(book, "content_type"):
            book.content_type = ct
        if hasattr(book, "book_type"):
            book.book_type = ct
        params = {"content_type": ct}
        task = GenerationTask(book_id=bid, task_type=TaskType.PREFACE, status=TaskStatus.PENDING, params=params)
        session.add(task)
        session.commit()
        session.refresh(task)
        tid = task.id
    except Exception as e:
        session.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        session.close()
    threading.Thread(target=_run_task_in_background, args=(bid, tid), daemon=True).start()
    return jsonify({"ok": True, "task_id": tid}), 202


@app.route("/api/books/<int:bid>/tasks/<int:tid>/cancel", methods=["POST"])
def cancel_task(bid, tid):
    """将运行中或等待中的任务标记为取消；引擎在下次检查时会中止并不写入结果。"""
    session = get_session()
    try:
        task = session.get(GenerationTask, tid)
        if not task or task.book_id != bid:
            return jsonify({"error": "任务不存在"}), 404
        if task.status not in (TaskStatus.PENDING, TaskStatus.RUNNING):
            return jsonify({"error": "任务已结束，无法取消"}), 400
        task.status = TaskStatus.CANCELLED
        session.commit()
        return jsonify({"ok": True})
    finally:
        session.close()


@app.route("/api/books/<int:bid>/chapters/from-outline", methods=["POST"])
def chapters_from_outline(bid):
    session = get_session()
    try:
        book = session.get(Book, bid)
        if not book:
            return jsonify({"error": "书籍不存在"}), 404
        outline = book.outline
        if not outline or not outline.raw_json:
            return jsonify({"error": "请先生成大纲"}), 400
        try:
            arr = json.loads(outline.raw_json)
        except Exception:
            return jsonify({"error": "大纲 JSON 解析失败"}), 400
        intro, fragments = _parse_outline_to_intro_and_fragments(outline.content or "")
        if getattr(outline, "intro", None) is not None:
            outline.intro = intro
        frag_by_idx = {idx: frag for idx, frag in fragments}
        created = []
        for i, item in enumerate(arr):
            idx = item.get("chapter_index", i + 1)
            title = item.get("title") or "第{}章".format(idx)
            frag = frag_by_idx.get(idx) or ""
            ch = Chapter(book_id=bid, order_index=idx, title=title)
            if hasattr(ch, "outline_fragment"):
                ch.outline_fragment = frag
            session.add(ch)
            session.flush()
            created.append({"id": ch.id, "order_index": ch.order_index, "title": ch.title})
        session.commit()
        return jsonify({"ok": True, "chapters": created})
    finally:
        session.close()


def _load_glossary(session, book_id):
    rows = session.execute(select(Term).where(Term.book_id == book_id).order_by(Term.id.asc())).scalars().all()
    return [{"term": r.term, "definition": r.definition or ""} for r in rows]


@app.route("/api/books/<int:bid>/chapters/<int:cid>/generate", methods=["POST"])
def generate_chapter(bid, cid):
    data = request.get_json() or {}
    revision_instruction = (data.get("revision_instruction") or "").strip() or None
    session = get_session()
    try:
        book = session.get(Book, bid)
        ch = session.get(Chapter, cid)
        if not book or not ch or ch.book_id != bid:
            return jsonify({"error": "书籍或章节不存在"}), 404
        # 优先用请求体中的 content_type（与当前下拉框一致）
        ct = (data.get("content_type") or getattr(book, "content_type", None) or "").strip().lower() or "academic"
        if ct not in ("academic", "novel"):
            ct = "academic"
        if hasattr(book, "content_type"):
            book.content_type = ct
        if hasattr(book, "book_type"):
            book.book_type = ct
        params = {"content_type": ct}
        if revision_instruction:
            params["revision_instruction"] = revision_instruction
        print("[generate-chapter] book_id=%s chapter_id=%s content_type=%s (from_request=%s)" % (bid, cid, ct, data.get("content_type")))
        task = GenerationTask(book_id=bid, chapter_id=cid, task_type=TaskType.CHAPTER, status=TaskStatus.PENDING, params=params)
        session.add(task)
        session.commit()
        session.refresh(task)
        tid = task.id
    except Exception as e:
        session.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        session.close()
    threading.Thread(target=_run_task_in_background, args=(bid, tid), daemon=True).start()
    return jsonify({"ok": True, "task_id": tid}), 202


@app.route("/api/books/<int:bid>/tasks/current", methods=["GET"])
def current_task(bid):
    session = get_session()
    try:
        running = list(session.execute(
            select(GenerationTask).where(GenerationTask.book_id == bid, GenerationTask.status == TaskStatus.RUNNING).order_by(GenerationTask.id.desc()).limit(1)
        ).scalars().all())
        failed = list(session.execute(
            select(GenerationTask).where(GenerationTask.book_id == bid, GenerationTask.status == TaskStatus.FAILED).order_by(GenerationTask.id.desc()).limit(1)
        ).scalars().all())
        out = {}
        if running:
            t = running[0]
            out["running"] = {
                "id": t.id,
                "task_type": t.task_type.value if hasattr(t.task_type, "value") else str(t.task_type),
                "progress_message": t.progress_message or "",
                "current_output": (t.current_output or "")[:50000],
            }
        # 仅在没有运行中任务时返回上次失败错误，避免旧错误盖住当前进度
        if failed and not running:
            out["last_error"] = failed[0].error_message or ""
        return jsonify(out)
    finally:
        session.close()


# ---------- 启动 ----------

if __name__ == "__main__":
    init_db()
    print("自动化写书 已启动： http://127.0.0.1:5000")
    print("关闭窗口或 Ctrl+C 停止服务。")
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
