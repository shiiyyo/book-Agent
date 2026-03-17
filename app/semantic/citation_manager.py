# -*- coding: utf-8 -*-
"""
参考文献与引用 (Citation Manager)
导入 PDF/Markdown 文献（Reference），写作时标注来源 ID（Citation），引文可追溯。
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import OPENAI_API_BASE, OPENAI_API_KEY, get_default_model
from app.semantic.reference_parser import ParsedReference, parse_local_text_file, parse_url_to_reference
from database.models import Reference


def _make_citation_key(prefix: str = "ref") -> str:
    ts = time.strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{ts}"


def add_reference_from_url(
    session: Session,
    *,
    book_id: int,
    url: str,
    citation_key: str | None = None,
    title: str | None = None,
    extract_chars: int = 4000,
    with_llm_summary: bool = True,
    model: str | None = None,
) -> Reference:
    ck = (citation_key or "").strip() or _make_citation_key("url")
    parsed = parse_url_to_reference(url, citation_key=ck, max_extract_chars=extract_chars)
    if title and title.strip():
        parsed.title = title.strip()
        parsed.meta = {**parsed.meta, "title": parsed.title}
    ref = Reference(
        book_id=book_id,
        title=parsed.title,
        citation_key=parsed.citation_key,
        file_path=None,
        content_extract=parsed.content_extract,
        meta=parsed.meta,
    )
    if with_llm_summary:
        _attach_llm_summary(ref, model=model)
    session.add(ref)
    session.flush()
    return ref


def add_reference_from_file(
    session: Session,
    *,
    book_id: int,
    file_path: str,
    citation_key: str | None = None,
    title: str | None = None,
    extract_chars: int = 4000,
    with_llm_summary: bool = True,
    model: str | None = None,
) -> Reference:
    ck = (citation_key or "").strip() or _make_citation_key("file")
    parsed = parse_local_text_file(file_path, citation_key=ck, max_extract_chars=extract_chars)
    if title and title.strip():
        parsed.title = title.strip()
        parsed.meta = {**parsed.meta, "title": parsed.title}
    ref = Reference(
        book_id=book_id,
        title=parsed.title,
        citation_key=parsed.citation_key,
        file_path=file_path,
        content_extract=parsed.content_extract,
        meta=parsed.meta,
    )
    if with_llm_summary:
        _attach_llm_summary(ref, model=model)
    session.add(ref)
    session.flush()
    return ref


def list_references(session: Session, *, book_id: int) -> list[Reference]:
    return list(session.execute(select(Reference).where(Reference.book_id == book_id).order_by(Reference.id.desc())).scalars().all())


def _attach_llm_summary(ref: Reference, *, model: str | None) -> None:
    """
    给 Reference.meta 附加 LLM 摘要与关键信息。
    - 若未配置模型或 API Key，则跳过（保持解析与生成解耦：可独立导入文献）
    """
    m = (model or get_default_model() or "").strip()
    if not m:
        return
    # 需要 API Key（走代理时也可能不需要，但这里保守：无 key 则不调用）
    if not (OPENAI_API_KEY or "").strip() and not (OPENAI_API_BASE or "").strip():
        return
    try:
        import litellm
    except Exception:
        return
    src = (ref.content_extract or "").strip()
    if not src:
        return
    src = re.sub(r"\s+", " ", src)[:3500]
    prompt = (
        "请将下面资料内容做“可引用”的结构化摘要（中文，严谨克制，不编造）：\n"
        "1) 一句话主题；2) 5-10 条关键要点（每条不超过40字）；3) 适用的引用场景（2-3条）；\n"
        "若资料不足以支撑断言，请写“待补充原文证据”。只输出 JSON：\n"
        '{"topic":"...","key_points":["..."],"use_cases":["..."]}'
        "\n\n资料摘录：\n"
        + src
    )
    kwargs = {"model": m, "messages": [{"role": "user", "content": prompt}], "max_tokens": 800}
    if (OPENAI_API_BASE or "").strip():
        kwargs["api_base"] = OPENAI_API_BASE.rstrip("/")
    if (OPENAI_API_KEY or "").strip():
        kwargs["api_key"] = OPENAI_API_KEY
    try:
        resp = litellm.completion(**kwargs)
        content = (resp.choices[0].message.content or "").strip()
        # 尝试取 JSON
        mjson = re.search(r"\{[\s\S]*\}", content)
        summary = mjson.group(0).strip() if mjson else content[:2000]
        meta = ref.meta or {}
        meta["llm_summary"] = summary
        ref.meta = meta
    except Exception:
        return
