# -*- coding: utf-8 -*-
"""兼容层：从 database.models 导出，保持 app.models 引用有效"""
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
    "Argument",
    "Base",
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
