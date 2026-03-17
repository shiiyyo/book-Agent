# -*- coding: utf-8 -*-
"""数据库连接、引擎与会话，支持 SQLite（默认）与 PostgreSQL"""
import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from database.models import Base

# 默认项目下 data 目录的 SQLite；可通过环境变量 DATABASE_URL 覆盖
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_DATA_DIR.mkdir(parents=True, exist_ok=True)
_DEFAULT_DB_PATH = _DATA_DIR / "academic_director.db"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{_DEFAULT_DB_PATH}")

# SQLite 多线程/多连接时避免 "database is locked"：
# - timeout：获取锁时等待秒数，超时后才报错（默认 5，改为 30）
# - WAL 模式：写不阻塞读、读不阻塞写，减少锁冲突
_SQLITE_CONNECT_ARGS = (
    {"check_same_thread": False, "timeout": 30}
    if "sqlite" in DATABASE_URL
    else {}
)

engine = create_engine(
    DATABASE_URL,
    connect_args=_SQLITE_CONNECT_ARGS,
    echo=os.getenv("SQL_ECHO", "").lower() in ("1", "true"),
)


@event.listens_for(engine, "connect")
def _sqlite_pragma(conn, connection_record):
    """SQLite 连接创建后启用 WAL 与 busy_timeout，减轻多线程下的 database is locked。"""
    if "sqlite" not in DATABASE_URL:
        return
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")  # 30 秒内等待锁
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db() -> None:
    """创建所有表，并为已有表补充 content_type、book_type、preface、出版目标与风格、outline 分章 等列（若不存在）。"""
    Base.metadata.create_all(bind=engine)
    ensure_content_type_column()
    ensure_book_type_column()
    ensure_preface_column()
    ensure_publisher_style_columns()
    ensure_academic_tone_column()
    ensure_outline_intro_column()
    ensure_chapter_outline_fragment_column()
    ensure_chapter_draft_content_column()
    ensure_chapter_approved_sections_column()


def ensure_content_type_column() -> None:
    """确保 books 表存在 content_type 列（SQLite）；若不存在则添加并回填。"""
    if "sqlite" not in DATABASE_URL:
        return
    try:
        from sqlalchemy import text
        with engine.begin() as conn:
            r = conn.execute(text("PRAGMA table_info(books)"))
            cols = [row[1] for row in r.fetchall()]
            if "content_type" not in cols:
                conn.execute(text("ALTER TABLE books ADD COLUMN content_type VARCHAR(32) DEFAULT 'academic'"))
                conn.execute(text("UPDATE books SET content_type = 'academic' WHERE content_type IS NULL"))
                print("[init_db] 已为 books 表添加 content_type 列")
    except Exception as e:
        print("[init_db] content_type 列检查/添加失败:", e)


def ensure_book_type_column() -> None:
    """确保 books 表存在 book_type 列（学术/小说 对应 academic/novel）；若不存在则添加并回填。"""
    if "sqlite" not in DATABASE_URL:
        return
    try:
        from sqlalchemy import text
        with engine.begin() as conn:
            r = conn.execute(text("PRAGMA table_info(books)"))
            cols = [row[1] for row in r.fetchall()]
            if "book_type" not in cols:
                conn.execute(text("ALTER TABLE books ADD COLUMN book_type VARCHAR(32) DEFAULT 'academic'"))
                conn.execute(text("UPDATE books SET book_type = COALESCE(content_type, 'academic')"))
                print("[init_db] 已为 books 表添加 book_type 列")
    except Exception as e:
        print("[init_db] book_type 列检查/添加失败:", e)


def ensure_preface_column() -> None:
    """确保 books 表存在 preface 列（前言）；若不存在则添加。"""
    if "sqlite" not in DATABASE_URL:
        return
    try:
        from sqlalchemy import text
        with engine.begin() as conn:
            r = conn.execute(text("PRAGMA table_info(books)"))
            cols = [row[1] for row in r.fetchall()]
            if "preface" not in cols:
                conn.execute(text("ALTER TABLE books ADD COLUMN preface TEXT"))
                print("[init_db] 已为 books 表添加 preface 列")
    except Exception as e:
        print("[init_db] preface 列检查/添加失败:", e)


def ensure_publisher_style_columns() -> None:
    """确保 books 表存在 target_publisher、writing_style、style_reference_text 列（出版目标与风格）。"""
    if "sqlite" not in DATABASE_URL:
        return
    try:
        from sqlalchemy import text
        with engine.begin() as conn:
            r = conn.execute(text("PRAGMA table_info(books)"))
            cols = [row[1] for row in r.fetchall()]
            if "target_publisher" not in cols:
                conn.execute(text("ALTER TABLE books ADD COLUMN target_publisher VARCHAR(256)"))
                print("[init_db] 已为 books 表添加 target_publisher 列")
            if "writing_style" not in cols:
                conn.execute(text("ALTER TABLE books ADD COLUMN writing_style TEXT"))
                print("[init_db] 已为 books 表添加 writing_style 列")
            if "style_reference_text" not in cols:
                conn.execute(text("ALTER TABLE books ADD COLUMN style_reference_text TEXT"))
                print("[init_db] 已为 books 表添加 style_reference_text 列")
    except Exception as e:
        print("[init_db] 出版目标与风格列检查/添加失败:", e)


def ensure_academic_tone_column() -> None:
    """确保 books 表存在 academic_tone 列（严谨学术 | 畅销书有学术味）。"""
    if "sqlite" not in DATABASE_URL:
        return
    try:
        from sqlalchemy import text
        with engine.begin() as conn:
            r = conn.execute(text("PRAGMA table_info(books)"))
            cols = [row[1] for row in r.fetchall()]
            if "academic_tone" not in cols:
                conn.execute(text("ALTER TABLE books ADD COLUMN academic_tone VARCHAR(32)"))
                print("[init_db] 已为 books 表添加 academic_tone 列")
    except Exception as e:
        print("[init_db] academic_tone 列检查/添加失败:", e)


def ensure_outline_intro_column() -> None:
    """确保 outlines 表存在 intro 列（大纲章前导语，与各章 outline_fragment 分开存储）。"""
    if "sqlite" not in DATABASE_URL:
        return
    try:
        from sqlalchemy import text
        with engine.begin() as conn:
            r = conn.execute(text("PRAGMA table_info(outlines)"))
            cols = [row[1] for row in r.fetchall()]
            if "intro" not in cols:
                conn.execute(text("ALTER TABLE outlines ADD COLUMN intro TEXT"))
                print("[init_db] 已为 outlines 表添加 intro 列")
    except Exception as e:
        print("[init_db] outline intro 列检查/添加失败:", e)


def ensure_chapter_outline_fragment_column() -> None:
    """确保 chapters 表存在 outline_fragment 列（该书大纲中本章对应的一段，便于按章局部修改）。"""
    if "sqlite" not in DATABASE_URL:
        return
    try:
        from sqlalchemy import text
        with engine.begin() as conn:
            r = conn.execute(text("PRAGMA table_info(chapters)"))
            cols = [row[1] for row in r.fetchall()]
            if "outline_fragment" not in cols:
                conn.execute(text("ALTER TABLE chapters ADD COLUMN outline_fragment TEXT"))
                print("[init_db] 已为 chapters 表添加 outline_fragment 列")
    except Exception as e:
        print("[init_db] chapter outline_fragment 列检查/添加失败:", e)


def ensure_chapter_draft_content_column() -> None:
    """确保 chapters 表存在 draft_content 列（章节草稿，独立于终稿 content）。"""
    if "sqlite" not in DATABASE_URL:
        return
    try:
        from sqlalchemy import text
        with engine.begin() as conn:
            r = conn.execute(text("PRAGMA table_info(chapters)"))
            cols = [row[1] for row in r.fetchall()]
            if "draft_content" not in cols:
                conn.execute(text("ALTER TABLE chapters ADD COLUMN draft_content TEXT"))
                print("[init_db] 已为 chapters 表添加 draft_content 列")
    except Exception as e:
        print("[init_db] chapter draft_content 列检查/添加失败:", e)


def ensure_chapter_approved_sections_column() -> None:
    """确保 chapters 表存在 approved_sections 列（JSON/TEXT，记录已审核通过的小节标题列表）。"""
    if "sqlite" not in DATABASE_URL:
        return
    try:
        from sqlalchemy import text
        with engine.begin() as conn:
            r = conn.execute(text("PRAGMA table_info(chapters)"))
            cols = [row[1] for row in r.fetchall()]
            if "approved_sections" not in cols:
                conn.execute(text("ALTER TABLE chapters ADD COLUMN approved_sections TEXT"))
                print("[init_db] 已为 chapters 表添加 approved_sections 列")
    except Exception as e:
        print("[init_db] chapter approved_sections 列检查/添加失败:", e)


def get_session() -> Session:
    return SessionLocal()
