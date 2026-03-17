# -*- coding: utf-8 -*-
"""
Academic-Director 数据库模型定义
学术书籍自动化创作系统 — 本地优先存储，支持 SQLite / PostgreSQL
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum as PyEnum
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Text,
    String,
    JSON,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

if TYPE_CHECKING:
    pass


class Base(DeclarativeBase):
    """所有模型的声明基类"""


# ---------- 枚举：书籍 / 任务 / 冲突状态 ----------

class BookStatus(str, PyEnum):
    """书籍项目状态"""
    DRAFT = "draft"
    OUTLINE_READY = "outline_ready"
    PIPELINE_RUNNING = "pipeline_running"
    PAUSED = "paused"
    COMPLETED = "completed"


class TaskType(str, PyEnum):
    """生成任务类型（对应流水线 Level 1/2/3 与审计、重写、前言）"""
    OUTLINE_LEVEL1 = "outline_l1"
    OUTLINE_LEVEL2 = "outline_l2"
    CHAPTER = "chapter"
    AUDIT = "audit"
    REWRITE = "rewrite"
    PREFACE = "preface"


class TaskStatus(str, PyEnum):
    """任务执行状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SUSPENDED = "suspended"
    CANCELLED = "cancelled"  # UI 强制中断后由 Worker 持久化


class ConflictStatus(str, PyEnum):
    """冲突裁决状态"""
    PENDING = "pending"
    RESOLVED_ACCEPT = "resolved_accept"
    RESOLVED_REJECT = "resolved_reject"
    RESOLVED_EDIT = "resolved_edit"


class ChapterStatus(str, PyEnum):
    """章节内容状态"""
    PENDING = "pending"
    DRAFT = "draft"
    AUDITED = "audited"
    FINAL = "final"


# ---------- 1. 书籍与大纲 ----------

class Book(Base):
    """书籍项目：书名、核心构思、全局参数（模型、语气）"""
    __tablename__ = "books"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    core_concept: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[BookStatus] = mapped_column(
        Enum(BookStatus), default=BookStatus.DRAFT, nullable=False
    )
    default_model: Mapped[str] = mapped_column(String(128), default="gpt-4o-mini")
    tone: Mapped[str] = mapped_column(String(64), default="严谨")
    content_type: Mapped[str] = mapped_column(String(32), default="academic", nullable=False)
    book_type: Mapped[str] = mapped_column(String(32), default="academic", nullable=False)
    academic_tone: Mapped[Optional[str]] = mapped_column(String(32))
    preface: Mapped[Optional[str]] = mapped_column(Text)
    target_publisher: Mapped[Optional[str]] = mapped_column(String(256))
    writing_style: Mapped[Optional[str]] = mapped_column(Text)
    style_reference_text: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    outline: Mapped[Optional["Outline"]] = relationship(back_populates="book", uselist=False)
    chapters: Mapped[list["Chapter"]] = relationship(
        back_populates="book", order_by="Chapter.order_index"
    )
    terms: Mapped[list["Term"]] = relationship(back_populates="book")
    arguments: Mapped[list["Argument"]] = relationship(back_populates="book")
    references: Mapped[list["Reference"]] = relationship(back_populates="book")
    tasks: Mapped[list["GenerationTask"]] = relationship(back_populates="book")
    conflicts: Mapped[list["Conflict"]] = relationship(back_populates="book")


class Outline(Base):
    """Level 1 全书大纲；intro 为章前导语，各章片段存于 Chapter.outline_fragment，content 为合并后的全文缓存"""
    __tablename__ = "outlines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    intro: Mapped[Optional[str]] = mapped_column(Text)
    raw_json: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    book: Mapped["Book"] = relationship(back_populates="outline")


# ---------- 2. 章节 ----------

class Chapter(Base):
    """章节：标题、论证细纲、正文、序号、状态"""
    __tablename__ = "chapters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    outline_fragment: Mapped[Optional[str]] = mapped_column(Text)
    outline_content: Mapped[Optional[str]] = mapped_column(Text)
    draft_content: Mapped[Optional[str]] = mapped_column(Text)
    approved_sections: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    content: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default=ChapterStatus.PENDING.value)
    word_count: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    book: Mapped["Book"] = relationship(back_populates="chapters")
    citations: Mapped[list["Citation"]] = relationship(back_populates="chapter")
    tasks: Mapped[list["GenerationTask"]] = relationship(back_populates="chapter")


# ---------- 3. 知识中枢：术语表 ----------

class Term(Base):
    """自动术语表：专有名词及定义，供后续章节强制一致"""
    __tablename__ = "terms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), nullable=False)
    term: Mapped[str] = mapped_column(String(256), nullable=False)
    definition: Mapped[Optional[str]] = mapped_column(Text)
    first_chapter_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("chapters.id", ondelete="SET NULL")
    )
    source: Mapped[Optional[str]] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    book: Mapped["Book"] = relationship(back_populates="terms")
    __table_args__ = (Index("ix_terms_book_term", "book_id", "term", unique=True),)


# ---------- 4. 论证链 ----------

class Argument(Base):
    """论证链：全书核心论点与推导逻辑"""
    __tablename__ = "arguments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), nullable=False)
    chapter_id: Mapped[Optional[int]] = mapped_column(ForeignKey("chapters.id", ondelete="SET NULL"))
    argument_text: Mapped[str] = mapped_column(Text, nullable=False)
    derivation_logic: Mapped[Optional[str]] = mapped_column(Text)
    order_in_chapter: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    book: Mapped["Book"] = relationship(back_populates="arguments")


# ---------- 5. 参考文献与引用 ----------

class Reference(Base):
    """参考文献：PDF/Markdown 导入，供 AI 标注来源"""
    __tablename__ = "references"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[Optional[str]] = mapped_column(String(512))
    citation_key: Mapped[str] = mapped_column(String(128), nullable=False)
    file_path: Mapped[Optional[str]] = mapped_column(String(1024))
    content_extract: Mapped[Optional[str]] = mapped_column(Text)
    meta: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    book: Mapped["Book"] = relationship(back_populates="references")
    citations: Mapped[list["Citation"]] = relationship(back_populates="reference")


class Citation(Base):
    """章节内引用：引文与文献关联，可追溯"""
    __tablename__ = "citations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chapter_id: Mapped[int] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False)
    reference_id: Mapped[int] = mapped_column(ForeignKey("references.id", ondelete="CASCADE"), nullable=False)
    location_in_text: Mapped[Optional[str]] = mapped_column(String(256))
    snippet: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    chapter: Mapped["Chapter"] = relationship(back_populates="citations")
    reference: Mapped["Reference"] = relationship(back_populates="citations")


# ---------- 6. 异步流水线任务 ----------

class GenerationTask(Base):
    """后台生成任务：支持断点续传与进度展示"""
    __tablename__ = "generation_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), nullable=False)
    chapter_id: Mapped[Optional[int]] = mapped_column(ForeignKey("chapters.id", ondelete="SET NULL"))
    task_type: Mapped[TaskType] = mapped_column(Enum(TaskType), nullable=False)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus), default=TaskStatus.PENDING, nullable=False
    )
    params: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    progress_message: Mapped[Optional[str]] = mapped_column(String(512))
    current_output: Mapped[Optional[str]] = mapped_column(Text, comment="当前生成中的文字，供前端轮询流式展示")
    checkpoint: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    book: Mapped["Book"] = relationship(back_populates="tasks")
    chapter: Mapped[Optional["Chapter"]] = relationship(back_populates="tasks")


# ---------- 7. 冲突与导演裁决 ----------

class Conflict(Base):
    """逻辑冲突：挂起任务并等待导演裁决"""
    __tablename__ = "conflicts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), nullable=False)
    task_id: Mapped[Optional[int]] = mapped_column(ForeignKey("generation_tasks.id", ondelete="SET NULL"))
    conflict_type: Mapped[str] = mapped_column(String(64))
    title: Mapped[Optional[str]] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text, nullable=False)
    context_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    resolution: Mapped[ConflictStatus] = mapped_column(
        Enum(ConflictStatus), default=ConflictStatus.PENDING, nullable=False
    )
    resolution_note: Mapped[Optional[str]] = mapped_column(Text)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    book: Mapped["Book"] = relationship(back_populates="conflicts")
