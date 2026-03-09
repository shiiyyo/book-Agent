# -*- coding: utf-8 -*-
"""
Academic-Director 数据库层
统一导出：Base、init_db、get_session，以及全部模型与枚举。
"""
from database.connection import get_session, init_db
from database.models import (
    Argument,
    Base,
    Book,
    BookStatus,
    Chapter,
    ChapterStatus,
    Citation,
    Conflict,
    ConflictStatus,
    GenerationTask,
    Outline,
    Reference,
    TaskStatus,
    TaskType,
    Term,
)

__all__ = [
    "Base",
    "init_db",
    "get_session",
    "Argument",
    "Book",
    "BookStatus",
    "Chapter",
    "ChapterStatus",
    "Citation",
    "Conflict",
    "ConflictStatus",
    "GenerationTask",
    "Outline",
    "Reference",
    "TaskStatus",
    "TaskType",
    "Term",
]
