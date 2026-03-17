# -*- coding: utf-8 -*-
"""
任务执行入口（编排层）

- 目前先薄封装 core.engine.run_task，后续可在此处加入：
  - 质量门控（重复检测、事实一致性）
  - 统一的引用/术语/论证链后处理
  - 按阶段的 prompt 版本管理
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from database.models import GenerationTask


def run_generation_task(
    session: Session,
    task: GenerationTask,
    *,
    glossary_terms: list[dict[str, Any]] | None = None,
) -> None:
    """执行生成任务（兼容旧引擎）。"""
    from core.engine import run_task

    run_task(session, task, glossary_terms=glossary_terms or None)

