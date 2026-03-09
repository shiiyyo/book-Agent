# -*- coding: utf-8 -*-
"""
Worker：持续轮询数据库中的 PENDING 任务，异步处理写作任务。
- 写章前：从 Glossary（Term 表）加载本书术语，传入 engine 作为上下文。
- 写章后：用 LLM 从本章正文中提取新术语并写入 Term 表，保证后续章节术语一致。
- API 调用失败由 engine 内重试；Worker 层仅做任务调度与术语前后处理。
"""
from dotenv import load_dotenv
load_dotenv()

import logging
import time
from datetime import datetime
from typing import Any

from sqlalchemy import select

from core.engine import run_task
from database import (
    Chapter,
    GenerationTask,
    TaskStatus,
    TaskType,
    Term,
    get_session,
)

logger = logging.getLogger(__name__)

POLL_INTERVAL = 5.0
ERROR_MESSAGE_MAX_LEN = 2000


def run_worker(poll_interval: float = POLL_INTERVAL) -> None:
    """
    无限循环：轮询 PENDING → 取一条 → 设为 RUNNING → 写章前加载术语、调用 engine、写章后提取术语入库 → 提交。
    支持 24 小时稳定运行，API 重试在 engine 内完成。
    """
    logger.info("Worker 启动，轮询间隔 %.1f 秒", poll_interval)
    while True:
        session = get_session()
        try:
            task = _fetch_one_pending(session)
            if not task:
                session.close()
                time.sleep(poll_interval)
                continue

            task.status = TaskStatus.RUNNING
            task.started_at = task.started_at or datetime.utcnow()
            session.commit()

            try:
                glossary_terms = _load_glossary_for_task(session, task)
                run_task(session, task, glossary_terms=glossary_terms)
                _save_extracted_terms_after_chapter(session, task)
                session.commit()
                logger.info("任务完成 task_id=%s type=%s", task.id, task.task_type)
            except Exception as e:
                session.rollback()
                _mark_failed(session, task.id, str(e))
                session.commit()
                logger.exception("任务失败 task_id=%s: %s", task.id, e)
        finally:
            session.close()
        time.sleep(0.5)


def _fetch_one_pending(session) -> "GenerationTask | None":
    """取一条 status=PENDING 的任务，按 created_at 升序。"""
    stmt = (
        select(GenerationTask)
        .where(GenerationTask.status == TaskStatus.PENDING)
        .order_by(GenerationTask.created_at.asc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def _load_glossary_for_task(session, task: GenerationTask) -> list[dict[str, Any]]:
    """写章前：从 Term 表加载本书术语，供 engine 作为 [Glossary] 上下文。仅对 chapter/rewrite 有效。"""
    if task.task_type not in (TaskType.CHAPTER, TaskType.REWRITE):
        return []

    rows = session.execute(
        select(Term).where(Term.book_id == task.book_id).order_by(Term.id.asc())
    ).scalars().all()
    return [{"term": r.term, "definition": r.definition or ""} for r in rows]


def _save_extracted_terms_after_chapter(session, task: GenerationTask) -> None:
    """写章后：学术模式提取术语；小说模式提取「新出现的伏笔」与「人物状态更新」，均写入 Term 表。"""
    if task.task_type not in (TaskType.CHAPTER, TaskType.REWRITE) or not task.chapter_id:
        return

    chapter = session.get(Chapter, task.chapter_id)
    if not chapter or not (chapter.content or "").strip():
        return

    from database.models import Book
    book = session.get(Book, task.book_id)
    model = book.default_model if book else None
    is_novel = (getattr(book, "content_type", None) or "").strip().lower() == "novel"

    try:
        if is_novel:
            from app.semantic.glossary import extract_fiction_entities
            extracted = extract_fiction_entities(chapter.content[:12000], model=model)
            source_tag = "fiction_entity"
        else:
            from app.semantic.glossary import extract_definitions_and_terms
            extracted = extract_definitions_and_terms(chapter.content[:12000], model=model)
            source_tag = "auto_extract"
    except ImportError:
        logger.warning("未找到 app.semantic.glossary，跳过本章提取")
        return
    except Exception as e:
        logger.warning("本章提取失败，跳过: %s", e)
        return

    existing = set(session.execute(select(Term.term).where(Term.book_id == task.book_id)).scalars().all())
    for item in extracted:
        if not item.term or item.term in existing:
            continue
        try:
            session.add(
                Term(
                    book_id=task.book_id,
                    term=item.term,
                    definition=item.definition,
                    first_chapter_id=task.chapter_id,
                    source=source_tag,
                )
            )
            existing.add(item.term)
        except Exception as e:
            logger.debug("入库跳过（可能重复）: %s", e)


def _mark_failed(session, task_id: int, message: str) -> None:
    """将指定任务标记为 FAILED 并写入错误信息。"""
    task = session.get(GenerationTask, task_id)
    if task:
        task.status = TaskStatus.FAILED
        task.error_message = (message or "")[:ERROR_MESSAGE_MAX_LEN]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    run_worker()
