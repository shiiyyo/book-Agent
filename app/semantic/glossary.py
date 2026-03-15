# -*- coding: utf-8 -*-
"""
自动术语表 (Glossary Engine)
从文本中用 LLM 识别「定义」与「专有名词」，供术语表入库与后续章节一致化使用。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from app.config import DEFAULT_MODELS, get_default_model


@dataclass
class ExtractedTerm:
    """从文本中识别出的专有名词及其定义（若有）"""
    term: str
    definition: str | None = None


def extract_definitions_and_terms(
    text: str,
    *,
    model: str | None = None,
    api_key: str | None = None,
) -> list[ExtractedTerm]:
    """
    利用 LLM 从文本中识别「定义」和「专有名词」，返回结构化列表。

    - 专有名词：学科/领域内的术语、概念名、缩写等。
    - 定义：文中对该术语的正式或非正式解释（若存在则填入 definition，否则为 None）。

    :param text: 待分析的原文（如章节正文）。
    :param model: 使用的模型，如 "gpt-4o-mini" / "openai/gpt-4o-mini" / "anthropic/claude-3-5-sonnet"。为 None 时用 DEFAULT_MODELS[0]。
    :param api_key: 可选，覆盖环境变量中的 API Key（按 provider 设置 OPENAI_API_KEY 或 ANTHROPIC_API_KEY）。
    :return: 列表，每项为 ExtractedTerm(term=专有名词, definition=定义或 None)。
    """
    if not text or not text.strip():
        return []

    model = model or get_default_model() or (DEFAULT_MODELS[0] if DEFAULT_MODELS else "gpt-4o-mini")
    prompt = _build_prompt(text)
    raw = _call_llm(prompt, model=model, api_key=api_key)
    return _parse_llm_response(raw)


def _build_prompt(text: str) -> str:
    return f"""你是一位学术写作助手。请从下面这段文本中识别出所有「专有名词」以及文中出现的「定义」。

要求：
1. 专有名词：学科/领域内的术语、概念名称、重要缩写等（如：Bell 态、量子纠缠、API）。
2. 定义：若文中对某术语给出了解释或定义，请把该定义原文或概括写在该术语下；若文中未给出定义，则定义字段留空。
3. 每个术语只出现一次；若同一术语在文中多处有不同表述的定义，可合并或取最完整的一条。
4. 严格按 JSON 格式输出，不要输出其他说明。格式如下：

```json
{{
  "items": [
    {{ "term": "专有名词1", "definition": "定义内容或空字符串" }},
    {{ "term": "专有名词2", "definition": "" }}
  ]
}}
```

待分析文本：

---
{text[:12000]}
---

请只输出上述 JSON，不要包含 ```json 之外的任何前后文字。"""


def extract_fiction_entities(
    text: str,
    *,
    model: str | None = None,
    api_key: str | None = None,
) -> list[ExtractedTerm]:
    """
    小说模式：从本章正文中提取「新出现的伏笔」与「人物状态更新」，供后续章节一致化使用。
    - term：伏笔名称/人物名或状态项（如「林羽的伤势」「王家的态度」）
    - definition：伏笔简述或人物当前状态/立场
    """
    if not text or not text.strip():
        return []

    model = model or get_default_model() or (DEFAULT_MODELS[0] if DEFAULT_MODELS else "gpt-4o-mini")
    prompt = f"""你是一位网文策划助手。请从下面这段小说章节正文中识别并列出：
1. **新出现的伏笔**：本章新埋下的悬念、未解之谜、后续可能回收的线索（用简短名称+一句话描述）。
2. **人物状态更新**：主要角色在本章结束时的状态、立场、情绪或关系变化（人名或「XX的状态」+ 简要描述）。

要求：
- 每个条目一个 term（伏笔名或人物/状态名）和一个 definition（一句话描述）。
- 严格按 JSON 格式输出，不要输出其他说明。格式如下：

```json
{{
  "items": [
    {{ "term": "伏笔或人物/状态名", "definition": "一句话描述" }},
    {{ "term": "另一项", "definition": "描述" }}
  ]
}}
```

待分析章节正文：

---
{text[:12000]}
---

请只输出上述 JSON，不要包含 ```json 之外的任何前后文字。"""
    raw = _call_llm(prompt, model=model, api_key=api_key)
    return _parse_llm_response(raw)


def _call_llm(prompt: str, *, model: str, api_key: str | None) -> str:
    try:
        import litellm
    except ImportError:
        raise RuntimeError("请安装 litellm: pip install litellm")

    messages = [{"role": "user", "content": prompt}]
    kwargs: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": 4096}
    if api_key:
        if "openai" in model.lower() or "gpt" in model.lower():
            kwargs["api_key"] = api_key
        elif "anthropic" in model.lower() or "claude" in model.lower():
            kwargs["api_key"] = api_key
    try:
        resp = litellm.completion(**kwargs)
        content = (resp.choices[0].message.content or "").strip()
        return content
    except Exception as e:
        raise RuntimeError(f"LLM 调用失败: {e}") from e


def _parse_llm_response(raw: str) -> list[ExtractedTerm]:
    """从 LLM 返回的字符串中解析出 items 列表，容错处理。"""
    if not raw:
        return []

    # 尝试从 ```json ... ``` 中取出内容
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw)
    if m:
        raw = m.group(1).strip()
    raw = raw.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []

    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []

    result: list[ExtractedTerm] = []
    for x in items:
        if not isinstance(x, dict):
            continue
        term = x.get("term")
        if not term or not str(term).strip():
            continue
        definition = x.get("definition")
        definition = str(definition).strip() if definition else None
        result.append(ExtractedTerm(term=str(term).strip(), definition=definition or None))
    return result
