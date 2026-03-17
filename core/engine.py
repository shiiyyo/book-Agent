# -*- coding: utf-8 -*-
"""
生成引擎：基于 LiteLLM 与学术写作 System Prompt，按任务类型生成大纲/章节/审计，
支持 API 重试，结果写回 DB。
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import (
    Book,
    BookStatus,
    Chapter,
    ChapterStatus,
    Citation,
    GenerationTask,
    Outline,
    Reference,
    Term,
    TaskStatus,
    TaskType,
)

logger = logging.getLogger(__name__)

# ---------- 学术书籍「灵魂」：追求「真」—— 事实支撑、论证严密 ----------
# 逻辑核心：论证一致性（术语不能乱）| 语言：严谨、被动句、客观、对冲 | 关键变量：参考文献、定义、推导逻辑 | AI 任务：提炼知识点，确保证据闭环
ACADEMIC_SYSTEM_PROMPT = """
# Role
你是一位拥有 20 年经验的资深学术图书主编，擅长逻辑构建、严谨论证及学术规范。你正在指导自动化写作流水线，生成**追求「真」**的学术专著正文：事实支撑、论证严密。

# 逻辑核心（与网络小说的根本区别）
- **论证一致性**：术语不能乱。严格遵守 [Glossary]（术语表），禁止对已定义概念做同义词替换（例如已定义为「分布式共识」则不得改为「去中心化协议」）。
- **证据闭环**：所有论述须基于 [Reference]（参考资料）；资料不足时以学术语气做推测性表述，不得断言。

# 书感与可读性（避免“论文感过重、章节重复多、读者推进力不足”）
- **避免跨章重复**：每章聚焦本章主题，与前章形成递进或展开，不要复述前文已讲过的定义或论证；新概念在本章首次出现时简要界定即可。
- **节奏与推进力**：适当使用过渡句、小节小结和“承上启下”句，增强读者推进感；避免连续大段纯论证，可穿插简短案例、数据或归纳句。
- **论证密度**：论证要有力但不宜堆砌；同一论点不重复展开，专有名词与机构名称全书前后一致。

# 语言风格
- 严谨、客观；可适度使用被动句与学术对冲语气（如 Suggests, Indicating, To some extent）。
- 避免情绪化与口语化。

# 关键变量与任务
- 输入：参考文献、定义、推导逻辑；[Current_Chapter_Outline] 与 [Pre-defined_Glossary]。
- **AI 任务**：提炼知识点，确保证据闭环；采用「提出主张 → 论据 → 论证 → 总结/过渡」结构。引用须标注 [Source_ID]。

# 输出规范
- 标准 Markdown；数学用 LaTeX（如 $E=mc^2$）；重点术语可加粗；不使用复杂格式或特殊字体颜色。
- **标题层级**：一级用「第一章」「第二章」；二级用「一、」「二、」；三级用「（一）」「（二）」；便于导出 Word 时统一排版。
- **字数（必须满足）**：默认按任务要求控制字数（例如 3000 字左右）。若任务未显式给出字数目标，则写到结构完整、论证收束。
"""

# ---------- 畅销书但有学术味：可读性强、有书感、仍讲清道理 ----------
BESTSELLER_ACADEMIC_SYSTEM_PROMPT = """
# Role
你是一位擅长写**畅销型知识书**的资深作者：既有学术底子、讲清逻辑与证据，又像在跟读者对话，好读、有节奏、有“书感”。你正在写的书面向更广读者，出版风格是“畅销书，但有学术味”。

# 与严谨学术专著的区别
- **语言**：少用被动句和生硬术语堆砌；多用短句、设问、适度口语化表达（如“我们不妨这样看”“这一点很重要”），但**不牺牲准确性**，关键概念和论据仍要严谨。
- **结构**：每章有清晰的故事线或问题线，读起来像在“被带着走”；适当用案例、场景或比喻开篇或收束，增强读者推进力。
- **论证**：论点与论据照常给出，引用可标注；但表述上避免“论文体”，同一论点不重复啰嗦，专有名词全书一致。

# 书感与可读性
- **避免跨章重复**：每章聚焦本章主题，与前章递进，不复述前文；新概念首次出现时简要界定即可。
- **节奏**：多用过渡句、小节小结；避免连续大段纯论证，可穿插简短例子或金句。
- **读者推进力**：让读者觉得“接着往下读有收获”，每节都有可抓取的要点或悬念。

# 关键变量与任务
- 输入：[Current_Chapter_Outline]、[Pre-defined_Glossary]、[Reference]。
- **AI 任务**：按细纲写出本章，**既有学术味（概念准确、有据可依）又像畅销书（好读、有书感）**；采用「引入 → 论据/案例 → 论证 → 小结/过渡」结构。引用须标注 [Source_ID]。

# 输出规范
- 标准 Markdown；术语可加粗；不使用复杂格式或特殊字体颜色。
- **标题层级**：一级「第一章」、二级「一、」、三级「（一）」。
- **字数（必须满足）**：默认按任务要求控制字数（例如 3000 字左右）。若任务未显式给出字数目标，则写到结构完整、叙述收束。
"""

# ---------- 网络小说「灵魂」：按 book_type=novel 切换使用 ----------
# 白金作家版：Show Don't Tell、节奏/断章悬念、人设维持(Character_Profiles)、黄金三章
FICTION_SYSTEM_PROMPT = """
# Role
你是一位拥有千万级点击量的顶级网络小说白金作家。你擅长构建宏大的世界观、细腻的人物刻画和极具张力的剧情推演。

# Writing Principles (小说写作原则)
1. **Show, Don't Tell**：不要直接说“他很生气”，要描写“他青筋暴起，指甲深陷入掌心”。
2. **节奏掌控**：遵循起承转合，在每一章结尾留下“断章”悬念（Cliffhanger），吸引读者继续。
3. **人设维持**：严格遵守 [Character_Profiles]。主角的行为逻辑必须符合其性格设定（如：腹黑、热血、或冷静）。
4. **黄金三章**：注重开头的情绪钩子，确保每一段描写都能增强代入感。

# Context Constraints
- **输入背景**：你会收到 [World_Setting]（世界观）、[Character_Cards]（人物卡）和 [Current_Plot_Outline]（本章细纲）。
- **任务目标**：将细纲扩充为富有画面感的正文，增加生动的对话和心理描写。
"""

# 兼容：引擎内统一用 FICTION_SYSTEM_PROMPT 作为网络小说 System Prompt
NOVEL_SYSTEM_PROMPT = FICTION_SYSTEM_PROMPT

# API 重试：次数与退避基数（秒）
LLM_MAX_RETRIES = 4
LLM_RETRY_BASE_DELAY = 2.0
# 单次 LLM 调用超时（秒），超时后抛出异常便于前端显示错误
LLM_REQUEST_TIMEOUT = 300
# 单次生成上限：8192 可减少长章被截断；若模型报 Invalid max_tokens 可改为 4096
LLM_MAX_TOKENS_CAP = 12000
# 单次 prompt 文本长度上限（字符），避免超长
PROMPT_GLOSSARY_MAX = 5000
PROMPT_REFERENCE_MAX = 4000
PROMPT_OUTLINE_MAX = 3000
PROMPT_STYLE_REFERENCE_MAX = 4000

# ---------- 字数控制（中文字符） ----------
# 目标：各阶段“约 3000 字”更稳定；默认统计口径为：中文字符 + 字母数字；忽略空白。
_RE_CJK = re.compile(r"[\u4e00-\u9fff]")
_RE_ALNUM = re.compile(r"[A-Za-z0-9]")


def _count_cn_chars(text: str) -> int:
    """统计“中文字符数”口径：CJK + 字母数字；忽略空白与常见标点。"""
    if not text:
        return 0
    s = text
    # 去掉空白
    s = re.sub(r"\s+", "", s)
    # CJK + alnum
    return len(_RE_CJK.findall(s)) + len(_RE_ALNUM.findall(s))


def _enforce_target_cn_len(
    session: Session,
    task: GenerationTask,
    model: str,
    base_messages: list[dict[str, str]],
    initial_text: str,
    *,
    target: int = 3000,
    min_len: int = 2800,
    max_len: int = 3300,
    max_rounds: int = 3,
    progress_hint: str = "补足字数",
) -> str:
    """
    若文本低于 min_len，则多轮“续写/扩写”到接近 target。
    - 不要求逐字精确，但尽量落入 [min_len, max_len]
    - 通过 stream_to_task 增量写入 task.current_output
    """
    text = (initial_text or "").strip()
    cur_len = _count_cn_chars(text)
    if cur_len >= min_len and cur_len <= max_len:
        return text
    if cur_len > max_len:
        # 过长：优先保留（避免反复压缩引入漂移），后续阶段再做裁剪
        return text

    for i in range(max_rounds):
        session.refresh(task)
        if task.status == TaskStatus.CANCELLED:
            task.progress_message = "已取消"
            session.commit()
            return text

        task.progress_message = f"字数不足（{cur_len}/{target}），正在补足…（第 {i + 1}/{max_rounds} 轮）"
        session.commit()

        # 续写提示：要求不重复、补论证/例子/过渡，保持原结构与标题层级。
        # 关键：禁止“为了凑字数重复生成同名小节”；若需扩写必须在原有小节内部向下扩展（增加（四）/（五）等更细标题或补充段落）。
        existing_headings = _extract_section_headings(text)
        headings_hint = ""
        if existing_headings:
            shown = "、".join(existing_headings[:24]) + ("…" if len(existing_headings) > 24 else "")
            headings_hint = f"\n\n【已存在的小节标题（禁止重复输出这些标题行）】\n{shown}\n"
        cont_user = (
            "请在不重复已有内容的前提下，继续补写并扩展上述文本，使其更完整：\n"
            "1) 保持原有标题与结构，不改变已写段落的含义；\n"
            "2) 优先补充论证链条、过渡句、案例或数据化说明；\n"
            "3) 不要引入与主题无关的新小节；\n"
            "4) **禁止**为了凑字数而重复生成已存在的小节标题或复述同一段落；\n"
            "5) 若需扩写，请在既有小节内部“向下生长”：补充更细层级标题（例如在「（一）」下增加「1）/2）」或「（四）」等）或补充段落；不要横向新增同级小节；\n"
            f"4) 目标总字数约 {target} 字（中文字符口径），补到接近即可。\n\n"
            + (headings_hint or "")
            + "请直接输出“新增补写的内容”，不要重写全文。"
        )
        messages = list(base_messages) + [
            {"role": "assistant", "content": text},
            {"role": "user", "content": cont_user},
        ]
        added = _call_llm_with_retry(
            session,
            task,
            messages,
            model,
            max_tokens=LLM_MAX_TOKENS_CAP,
            progress_hint=progress_hint,
            stream_to_task=True,
        )
        added = (added or "").strip()
        if added:
            text = (text.rstrip() + "\n\n" + added.lstrip()).strip()
        cur_len = _count_cn_chars(text)
        if cur_len >= min_len:
            break
    return text


def _extract_section_headings(text: str) -> list[str]:
    """
    提取常见标题行（用于补写时提示“不要重复标题”）。
    - Markdown: ##/### ...
    - 中文层级：一、二、…；（一）（二）…
    """
    if not text:
        return []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    seen = set()
    for line in lines:
        s = (line or "").strip()
        if not s:
            continue
        m = re.match(r"^(#{1,6})\s+(.+)$", s)
        if m:
            t = (m.group(2) or "").strip()
            if t and t not in seen:
                out.append(t)
                seen.add(t)
            continue
        if re.match(r"^[一二三四五六七八九十]+[、．.]\s*.+$", s) or re.match(r"^[（(][一二三四五六七八九十]+[)）]\s*.+$", s):
            if s not in seen:
                out.append(s)
                seen.add(s)
    return out

# 内容类型
CONTENT_TYPE_ACADEMIC = "academic"
CONTENT_TYPE_NOVEL = "novel"
# 学术写作风格：严谨学术 | 畅销书有学术味
ACADEMIC_TONE_STRICT = "strict"
ACADEMIC_TONE_BESTSELLER = "bestseller"


def _get_academic_system_prompt(book: Book) -> str:
    """学术类书籍按用户选择的风格返回对应 System Prompt。"""
    tone = getattr(book, "academic_tone", None) and (book.academic_tone or "").strip().lower()
    if tone == ACADEMIC_TONE_BESTSELLER:
        return BESTSELLER_ACADEMIC_SYSTEM_PROMPT
    return ACADEMIC_SYSTEM_PROMPT


def _is_bestseller_academic(book: Book) -> bool:
    """是否为「畅销书但有学术味」风格（仅当 content_type 为学术时有效）。"""
    if _is_novel(book):
        return False
    tone = getattr(book, "academic_tone", None) and (book.academic_tone or "").strip().lower()
    return tone == ACADEMIC_TONE_BESTSELLER


def _format_publisher_style(book: Book) -> str:
    """根据书籍的目标出版社、风格描述与参考范文，拼成一段注入 prompt 的说明。"""
    parts = []
    pub = getattr(book, "target_publisher", None) and (book.target_publisher or "").strip()
    style = getattr(book, "writing_style", None) and (book.writing_style or "").strip()
    ref = getattr(book, "style_reference_text", None) and (book.style_reference_text or "").strip()
    if pub:
        parts.append("**目标出版社**：{}".format(pub))
    if style:
        parts.append("**出版/写作风格要求**：{}".format(style))
    if ref:
        sample = ref[:PROMPT_STYLE_REFERENCE_MAX] + ("…" if len(ref) > PROMPT_STYLE_REFERENCE_MAX else "")
        parts.append("**参考范文（请模仿其语气、段落结构与用词风格，用于统一全书风格；可来自读秀等渠道）**：\n\n" + sample)
    if not parts:
        return ""
    return "\n\n【出版目标与风格】\n" + "\n".join(parts)


def _normalize_paragraph_spacing(text: str | None) -> str:
    """标准化书籍段落格式：去除 * 号、统一换行、多处空行合并为一段一空行。"""
    if not text or not isinstance(text, str):
        return (text or "").strip()
    s = text
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("**", "").replace("*", "")
    s = re.sub(r"\n[\s]*\n[\s]*\n+", "\n\n", s)
    s = re.sub(r"\n{2,}", "\n\n", s)
    return s.strip()


def _is_novel(book: Book) -> bool:
    """根据书籍的 content_type 判断是否为网络小说；未设置或空则视为学术专著。"""
    ct = getattr(book, "content_type", None)
    if ct is None or (isinstance(ct, str) and not ct.strip()):
        return False
    return str(ct).strip().lower() == CONTENT_TYPE_NOVEL


def run_task(
    session: Session,
    task: GenerationTask,
    glossary_terms: list[dict[str, Any]] | None = None,
) -> None:
    """
    执行单条生成任务：按 task_type 调用 LiteLLM，结果写回 DB 并置为 COMPLETED。
    若 UI 已将该任务设为 CANCELLED，则刷新后直接返回。
    glossary_terms: 仅对 chapter/rewrite 有效，格式 [{"term": "...", "definition": "..."}, ...]，由 worker 在写章前从 Term 表加载传入。
    不在此处 commit，由调用方（worker）负责提交。
    """
    session.refresh(task)
    if task.status == TaskStatus.CANCELLED:
        task.progress_message = "已取消"
        return

    book = session.get(Book, task.book_id)
    if not book:
        raise ValueError(f"Book id={task.book_id} 不存在")
    session.refresh(book)
    # 优先用任务创建时写入的 content_type；兼容 params 为 dict 或未反序列化的 str
    _params = task.params
    if isinstance(_params, str):
        try:
            _params = json.loads(_params) if _params.strip() else {}
        except Exception:
            _params = {}
    if not isinstance(_params, dict):
        _params = {}
    content_type = (_params.get("content_type") or (getattr(book, "content_type", None) or "").strip().lower() or "academic")
    if isinstance(content_type, str):
        content_type = content_type.strip().lower() or "academic"
    else:
        content_type = "academic"
    if content_type not in (CONTENT_TYPE_ACADEMIC, CONTENT_TYPE_NOVEL):
        content_type = CONTENT_TYPE_ACADEMIC
    book.content_type = content_type
    print(f"DEBUG: Current book type is {content_type!r} (from task.params={_params.get('content_type')!r}, book.content_type={getattr(book, 'content_type', None)!r})")
    logger.info("run_task book_id=%s content_type=%s task_type=%s params=%s", book.id, content_type, task.task_type, _params)

    model = _resolve_model(book, task)
    task.progress_message = "准备生成…"
    session.commit()

    if task.task_type == TaskType.OUTLINE_LEVEL1:
        if _params.get("partial_revision") and _params.get("revision_instruction") and book.outline and (book.outline.content or "").strip():
            _run_outline_partial_revision(session, task, book, model)
        else:
            _run_outline_l1(session, task, book, model)
    elif task.task_type == TaskType.OUTLINE_LEVEL2:
        _run_outline_l2(session, task, book, model)
    elif task.task_type == TaskType.PREFACE:
        _run_preface(session, task, book, model)
    elif task.task_type == TaskType.CHAPTER:
        _run_chapter(session, task, book, model, glossary_terms or [])
    elif task.task_type == TaskType.REWRITE:
        _run_rewrite(session, task, book, model, glossary_terms or [])
    elif task.task_type == TaskType.AUDIT:
        _run_audit(session, task, book, model)
    else:
        raise ValueError(f"未知任务类型: {task.task_type}")

    session.refresh(task)
    if task.status == TaskStatus.CANCELLED:
        task.progress_message = "已取消"
        return
    task.status = TaskStatus.COMPLETED
    task.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    task.progress_message = "已完成"
    task.error_message = None
    # 兜底：立即落库，避免线程退出/异常导致 UI 卡在“运行中”
    session.commit()


def _resolve_model(book: Book, task: GenerationTask) -> str:
    """LiteLLM 使用的模型名：优先 .env（OPENAI_MODEL / DEFAULT_MODEL），否则 task.params 或 book.default_model；绝不使用 DeepSeek/Moonshot。"""
    from app.config import get_default_model, OPENAI_API_BASE
    raw = get_default_model()
    if raw:
        raw = raw.strip()
    if not raw and task.params and isinstance(task.params, dict) and task.params.get("model"):
        raw = str(task.params["model"]).strip()
    if not raw and getattr(book, "default_model", None):
        raw = (book.default_model or "").strip()
    if raw and (raw.startswith("deepseek/") or raw.startswith("moonshot/")):
        raw = ""
    if not raw:
        raise ValueError(
            "未配置调用模型。请在 .env 中设置 OPENAI_MODEL 或 DEFAULT_MODEL 后重启服务，例如：\n"
            "OPENAI_MODEL=openai/grok-4-0709  或  OPENAI_MODEL=claude-3-7-sonnet-20250219\n"
            "（可复制 .env.example 为 .env 后修改）"
        )
    # 若已配置 OPENAI_API_BASE（代理），强制走 openai/ 前缀，避免 LiteLLM 误路由到 DeepSeek 等
    if OPENAI_API_BASE and OPENAI_API_BASE.strip():
        if not raw.startswith("openai/"):
            raw = "openai/" + raw
    return raw


def _call_llm_with_retry(
    session: Session,
    task: GenerationTask,
    messages: list[dict[str, str]],
    model: str,
    max_tokens: int | None = None,
    progress_hint: str | None = None,
    stream_to_task: bool = False,
) -> str:
    """带重试的 LiteLLM 调用，失败时更新 task.progress_message 并 commit 以便前端轮询可见。progress_hint 会显示在进度中，便于用户判断当前步骤。"""
    import litellm
    from app.config import OPENAI_API_BASE, OPENAI_API_KEY

    # 调试：确认实际使用的模型（控制台可见）
    print("[LiteLLM] model=%r api_base=%r" % (model, OPENAI_API_BASE[:50] + "..." if OPENAI_API_BASE and len(OPENAI_API_BASE) > 50 else OPENAI_API_BASE), flush=True)

    last_error: Exception | None = None
    capped = min(max(1, int(max_tokens) if max_tokens is not None else LLM_MAX_TOKENS_CAP), LLM_MAX_TOKENS_CAP)
    kwargs: dict[str, Any] = {
        "timeout": LLM_REQUEST_TIMEOUT,
        "max_tokens": capped,
    }
    # 强制走代理：显式传 api_base 与 api_key，避免 LiteLLM 按模型名误路由到 DeepSeek 等
    if OPENAI_API_BASE and OPENAI_API_BASE.strip():
        base = OPENAI_API_BASE.rstrip("/")
        kwargs["api_base"] = base
        if OPENAI_API_KEY and OPENAI_API_KEY.strip():
            kwargs["api_key"] = OPENAI_API_KEY
    hint_suffix = ("（" + progress_hint + "）") if progress_hint else ""
    for attempt in range(LLM_MAX_RETRIES):
        try:
            if attempt == 0:
                task.progress_message = "调用模型中…" + hint_suffix
            else:
                task.progress_message = f"API 曾失败，正在重试（第 {attempt + 1}/{LLM_MAX_RETRIES} 次尝试）…" + hint_suffix
            if stream_to_task:
                # Only reset when starting fresh; if caller already put existing text into
                # current_output (e.g., enforcing length by appending), keep it.
                if not (task.current_output or "").strip():
                    task.current_output = ""
            session.commit()
            if stream_to_task:
                # Best-effort streaming: incrementally append deltas into task.current_output
                # and commit periodically so SSE can push updates.
                buf_parts: list[str] = []
                last_commit_at = time.time()
                resp_iter = litellm.completion(model=model, messages=messages, stream=True, **kwargs)
                for chunk in resp_iter:
                    delta = ""
                    try:
                        # OpenAI-like shape: choices[0].delta.content
                        delta = (chunk.choices[0].delta.get("content") if hasattr(chunk.choices[0], "delta") else "") or ""
                    except Exception:
                        try:
                            delta = (chunk.choices[0].delta.content or "")
                        except Exception:
                            delta = ""
                    if not delta:
                        continue
                    buf_parts.append(delta)
                    task.current_output = (task.current_output or "") + delta
                    now = time.time()
                    if now - last_commit_at >= 0.35 or len(task.current_output) % 400 == 0:
                        session.commit()
                        last_commit_at = now
                    session.refresh(task)
                    if task.status == TaskStatus.CANCELLED:
                        task.progress_message = "已取消"
                        session.commit()
                        return ""
                content = ("".join(buf_parts) or (task.current_output or "")).strip()
            else:
                resp = litellm.completion(model=model, messages=messages, **kwargs)
                content = (resp.choices[0].message.content or "").strip()
            if not content:
                raise ValueError("模型返回为空")
            return content
        except Exception as e:
            last_error = e
            err_short = (str(e)[:60] + "…") if len(str(e)) > 60 else str(e)
            logger.warning("LLM 调用失败 (attempt %s): %s", attempt + 1, e)
            if attempt < LLM_MAX_RETRIES - 1:
                delay = LLM_RETRY_BASE_DELAY * (2**attempt)
                task.progress_message = f"API 调用失败（{err_short}），{delay:.0f}s 后第 {attempt + 2}/{LLM_MAX_RETRIES} 次尝试…{hint_suffix}"
                session.commit()
                time.sleep(delay)
    err_msg = str(last_error) if last_error else "未知错误"
    if "timeout" in err_msg.lower() or "timed out" in err_msg.lower():
        err_msg = f"模型调用超时（{LLM_REQUEST_TIMEOUT}s），请检查网络或 API 配置后重试。原始: {err_msg}"
    raise RuntimeError(f"LLM 调用在 {LLM_MAX_RETRIES} 次重试后仍失败: {err_msg}") from last_error


def _parse_outline_to_intro_and_fragments(content: str) -> tuple[str, list[tuple[int, str]]]:
    """将全书大纲按「## 第 N 章」拆成：章前导语 intro + [(order_index, fragment), ...]。"""
    if not content or not content.strip():
        return "", []
    text = content.strip()
    lines = text.split("\n")
    intro_parts: list[str] = []
    fragments: list[tuple[int, str]] = []
    chapter_head = re.compile(r"^#+\s*第\s*(\d+)\s*章")
    current_index: int | None = None
    current_body: list[str] = []

    for line in lines:
        m = chapter_head.match(line.strip())
        if m:
            if current_index is not None:
                fragments.append((current_index, "\n".join(current_body).strip()))
            current_index = int(m.group(1))
            current_body = [line]
        else:
            if current_index is not None:
                current_body.append(line)
            else:
                intro_parts.append(line)
    if current_index is not None:
        fragments.append((current_index, "\n".join(current_body).strip()))
    intro = "\n".join(intro_parts).strip()
    return intro, fragments


def _build_outline_content(intro: str, chapters_sorted: list[Chapter]) -> str:
    """用 intro + 各章 outline_fragment 按顺序拼成全书大纲 content。"""
    parts = [intro] if intro else []
    for ch in chapters_sorted:
        frag = getattr(ch, "outline_fragment", None) and (ch.outline_fragment or "").strip()
        if frag:
            parts.append(frag)
    return "\n\n".join(parts).strip() or ""


def _run_outline_l1(session: Session, task: GenerationTask, book: Book, model: str) -> None:
    """Level 1：书名 + 核心构思 → 全书大纲。支持 revision_instruction；按 content_type 区分学术/网文。"""
    task.progress_message = "生成全书大纲…"
    session.commit()

    revision = ""
    if task.params and isinstance(task.params, dict) and task.params.get("revision_instruction"):
        rev_text = str(task.params["revision_instruction"]).strip()
        revision = "\n\n【用户明确要求，必须优先满足】\n用户对大纲的修改意图：{}\n请严格按上述意图调整大纲内容，并在输出中体现每一条要求。".format(rev_text)

    if _is_novel(book):
        user_content = f"""请根据以下信息，生成一部网络小说的全书大纲（Markdown 格式）。

**书名**：{book.title}

**核心构思 / 故事梗概**：
{book.core_concept or "（未提供）"}

要求：
1. 输出为 Markdown，包含一级标题（# 书名）和若干二级标题（## 第 N 章 标题），每章下用 1–2 句话写出本章要点或情节点。
2. 结构按剧情发展设计：起承转合、主线清晰，每章结尾可注明预期悬念或爽点。
3. 末尾附一段 JSON 数组，格式为 [{{"chapter_index": 1, "title": "第一章 标题", "summary": "本章一句话梗概"}}, ...]，便于程序解析。若无法输出 JSON 可省略。{revision}"""
        system_msg = "你是资深网文策划，擅长设计长篇连载的大纲与节奏，章节划分清晰、每章有看点。"
    else:
        user_content = f"""请根据以下信息，生成一本学术专著的全书大纲（Markdown 格式）。

**书名**：{book.title}

**核心构思 / 研究假设**：
{book.core_concept or "（未提供）"}

要求：
1. 输出为 Markdown，包含一级标题（# 书名）和若干二级标题（## 第 N 章 标题），每章下可简要列出 2–4 个小节或要点。
2. **全书建议 6–10 章**，章数过少不利于展开，过多则易松散；结构需体现逻辑递进，适合学术专著。
3. 末尾附一段 JSON 数组，格式为 [{{"chapter_index": 1, "title": "第一章 标题", "summary": "简短说明"}}, ...]，便于程序解析。若无法输出 JSON 可省略。{revision}"""
        if _is_bestseller_academic(book):
            system_msg = "你是畅销型知识书策划，擅长设计既有逻辑又好看的大纲：结构清晰、有递进感、便于读者推进，适合「畅销书但有学术味」的出版风格。输出使用 Markdown，逻辑清晰。"
        else:
            system_msg = "你是一位资深学术图书策划编辑，擅长根据书名与核心构思设计严谨的专著大纲。输出使用 Markdown，逻辑清晰。"
    style_block = _format_publisher_style(book)
    if style_block:
        user_content = user_content + "\n\n" + style_block

    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_content[:12000]},
    ]
    content = _call_llm_with_retry(session, task, messages, model, progress_hint="全书大纲", stream_to_task=True)

    session.refresh(task)
    if task.status == TaskStatus.CANCELLED:
        return

    raw_json_str: str | None = None
    json_match = re.search(r"\[[\s\S]*?\{\s*\"chapter_index\"[\s\S]*?\}\s*\]", content)
    if json_match:
        try:
            raw_json_str = json_match.group(0).strip()
            json.loads(raw_json_str)
            # 保存给用户看的大纲只保留 Markdown 部分，不包含 JSON
            content = content.replace(raw_json_str, "").strip()
        except json.JSONDecodeError:
            raw_json_str = None

    content = _normalize_paragraph_spacing(content)
    # 字数控制：全书大纲目标 ~3000 中文字符（不含末尾 JSON）
    task.current_output = content
    content = _enforce_target_cn_len(
        session,
        task,
        model,
        messages,
        content,
        target=3000,
        min_len=2800,
        max_len=3300,
        max_rounds=3,
        progress_hint="补足大纲字数",
    )
    content = _normalize_paragraph_spacing(content)
    intro, fragments = _parse_outline_to_intro_and_fragments(content)
    if book.outline:
        book.outline.content = content
        book.outline.raw_json = raw_json_str
        if getattr(book.outline, "intro", None) is not None or intro:
            try:
                book.outline.intro = intro
            except AttributeError:
                pass
    else:
        outline = Outline(book_id=book.id, content=content, raw_json=raw_json_str)
        try:
            outline.intro = intro
        except AttributeError:
            pass
        session.add(outline)
    session.flush()
    ch_list = session.execute(select(Chapter).where(Chapter.book_id == book.id).order_by(Chapter.order_index)).scalars().all()
    chapters = sorted(ch_list, key=lambda c: c.order_index)
    frag_by_idx = {idx: frag for idx, frag in fragments}
    for ch in chapters:
        if ch.order_index in frag_by_idx and hasattr(ch, "outline_fragment"):
            ch.outline_fragment = frag_by_idx[ch.order_index]
    session.flush()
    if chapters:
        try:
            book.outline.intro = intro
        except AttributeError:
            pass
        rebuilt = _build_outline_content(intro, chapters)
        if rebuilt:
            book.outline.content = rebuilt
    book.status = BookStatus.OUTLINE_READY
    task.current_output = content


def _run_outline_partial_revision(session: Session, task: GenerationTask, book: Book, model: str) -> None:
    """仅按用户修改意图局部调整大纲：只改受影响章节的 outline_fragment，再合并，不改动其他章。"""
    task.progress_message = "正在按修改意图局部调整大纲…"
    session.commit()

    rev_text = (task.params or {}).get("revision_instruction") if isinstance(task.params, dict) else None
    rev_text = (rev_text or "").strip()
    if not rev_text:
        raise ValueError("局部修改需要提供 revision_instruction")
    chapters = sorted(session.execute(select(Chapter).where(Chapter.book_id == book.id).order_by(Chapter.order_index)).scalars().all(), key=lambda c: c.order_index)
    intro = getattr(book.outline, "intro", None) if book.outline else None
    intro = (intro or "").strip()
    has_fragments = any(getattr(ch, "outline_fragment", None) and (ch.outline_fragment or "").strip() for ch in chapters)
    if not book.outline or not (book.outline.content or "").strip():
        raise ValueError("当前无大纲内容，请先生成全书大纲后再使用局部修改")

    if has_fragments and chapters:
        current_full = _build_outline_content(intro or "", chapters) or (book.outline.content or "")
        user_content = f"""当前全书大纲（按章分开）。用户**仅希望做以下局部修改**，请只输出**需要修改或新增的那一章（或几章）**的完整片段，其余章节不要输出。

【用户修改意图】
{rev_text}

【当前全书大纲（供参考）】
{current_full[:6000]}

请**只**输出被修改或新增的章节的 Markdown 片段（以 ## 第 N 章 开头，到下一章之前或结尾）。若用户要求「增加一章」，请输出新章节的完整片段并注明「第几章」；若只改某章，请只输出该章修改后的完整片段。不要输出未修改的章节。"""
        style_block = _format_publisher_style(book)
        if style_block:
            user_content = user_content + "\n\n" + style_block
        system_msg = "你是图书策划编辑。用户只要求局部修改大纲（如增加某章、改某章标题或要点）。你只输出被修改或新增的章节片段（## 第 N 章 ...），一段或多段，不要输出未改动的章节。"
        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_content[:12000]},
        ]
        content = _call_llm_with_retry(session, task, messages, model, progress_hint="大纲局部修改", stream_to_task=True)
        session.refresh(task)
        if task.status == TaskStatus.CANCELLED:
            return
        content = _normalize_paragraph_spacing(content)
        parsed_intro, parsed_fragments = _parse_outline_to_intro_and_fragments(content)
        for idx, frag in parsed_fragments:
            ch = next((c for c in chapters if c.order_index == idx), None)
            if ch is not None and hasattr(ch, "outline_fragment"):
                ch.outline_fragment = frag
            elif ch is None:
                new_ch = Chapter(book_id=book.id, order_index=idx, title="第{}章".format(idx), outline_fragment=frag)
                session.add(new_ch)
        session.flush()
        if parsed_intro and not intro:
            try:
                book.outline.intro = parsed_intro
                intro = parsed_intro
            except AttributeError:
                pass
        chapters = sorted(session.execute(select(Chapter).where(Chapter.book_id == book.id).order_by(Chapter.order_index)).scalars().all(), key=lambda c: c.order_index)
        rebuilt = _build_outline_content(intro or "", chapters)
        if rebuilt:
            book.outline.content = rebuilt
        book.status = BookStatus.OUTLINE_READY
        task.current_output = rebuilt or book.outline.content
        return

    current_outline = (book.outline.content or "").strip()
    user_content = f"""以下是当前全书大纲。用户**仅希望做以下局部修改**，请只完成这些修改，**其余章节与表述尽量保持原样**，不要重写、删改或合并未提及的部分。

【用户修改意图】
{rev_text}

【当前大纲】
{current_outline[:8000]}

请直接输出修改后的完整大纲（Markdown，格式与当前一致）。若原大纲末尾有 JSON 数组（chapter_index/title/summary），可在新大纲末尾保留或更新该 JSON；若无法输出 JSON 可省略。"""
    style_block = _format_publisher_style(book)
    if style_block:
        user_content = user_content + "\n\n" + style_block
    system_msg = "你是图书策划编辑。用户只要求对大纲做局部修改（如增加某章、改某章标题、删某节等），请严格只完成这些修改，其他部分尽量一字不改地保留。"
    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_content[:14000]},
    ]
    content = _call_llm_with_retry(session, task, messages, model, progress_hint="大纲局部修改", stream_to_task=True)
    session.refresh(task)
    if task.status == TaskStatus.CANCELLED:
        return
    raw_json_str: str | None = None
    json_match = re.search(r"\[[\s\S]*?\{\s*\"chapter_index\"[\s\S]*?\}\s*\]", content)
    if json_match:
        try:
            raw_json_str = json_match.group(0).strip()
            json.loads(raw_json_str)
            content = content.replace(raw_json_str, "").strip()
        except json.JSONDecodeError:
            raw_json_str = None
    content = _normalize_paragraph_spacing(content)
    intro, fragments = _parse_outline_to_intro_and_fragments(content)
    if book.outline:
        book.outline.content = content
        if raw_json_str is not None:
            book.outline.raw_json = raw_json_str
        if hasattr(book.outline, "intro") and intro:
            book.outline.intro = intro
    chapters = sorted(session.execute(select(Chapter).where(Chapter.book_id == book.id).order_by(Chapter.order_index)).scalars().all(), key=lambda c: c.order_index)
    frag_by_idx = {idx: frag for idx, frag in fragments}
    for ch in chapters:
        if ch.order_index in frag_by_idx and hasattr(ch, "outline_fragment"):
            ch.outline_fragment = frag_by_idx[ch.order_index]
    book.status = BookStatus.OUTLINE_READY
    task.current_output = content


def _run_preface(session: Session, task: GenerationTask, book: Book, model: str) -> None:
    """根据书名、核心构思（及可选大纲摘要）生成约 3000 字前言，吸引读者。"""
    task.progress_message = "生成前言…"
    session.commit()

    outline_hint = ""
    if book.outline and (book.outline.content or "").strip():
        outline_hint = "\n**全书大纲摘要（供把握整体结构）**：\n" + (book.outline.content or "").strip()[:1200] + ("…" if len((book.outline.content or "").strip()) > 1200 else "")

    if _is_novel(book):
        user_content = f"""请为以下网络小说撰写**前言/作者的话**（约 3000 字），用于正式章节之前，吸引读者继续读下去。

**书名**：{book.title}

**核心构思 / 故事梗概**：
{book.core_concept or "（未提供）"}
{outline_hint}

要求：
1. 语气亲切、有代入感，可略带悬念或情绪钩子，让读者想立刻进入正文。
2. 可简要交代创作缘起、本书特色或与读者的约定，但不要剧透关键情节。
3. 字数约 2800–3200 字（中文字符），直接输出前言正文，不要输出「前言」标题或章节号。
4. 使用 Markdown 分段，段落适中，便于阅读。"""
        system_msg = "你是资深网文作者，擅长写吸引人的前言与作者的话，能让读者产生强烈阅读欲望。"
    else:
        user_content = f"""请为以下学术专著撰写**前言**（约 3000 字），用于正文之前，说明本书的写作缘起、目标读者与核心价值，吸引人继续阅读。

**书名**：{book.title}

**核心构思 / 研究假设**：
{book.core_concept or "（未提供）"}
{outline_hint}

要求：
1. 开篇可点明问题或时代背景，说明为何要写这本书、解决什么问题。
2. 明确目标读者（学者、从业者、爱好者等）以及读者将获得什么。
3. 简要概括全书结构与逻辑线索，但不替代目录，保持可读性与感染力。
4. 字数约 2800–3200 字（中文字符），直接输出前言正文，不要输出「前言」标题或章节号。
5. 使用 Markdown 分段，语气严谨但不枯燥，能吸引非专业读者产生兴趣。"""
        if _is_bestseller_academic(book):
            system_msg = "你是畅销型知识书作者，写前言要有对话感、好读、有书感，同时点明本书的价值与逻辑，吸引更广读者；既有学术味又不掉书袋。"
        else:
            system_msg = "你是资深学术图书策划或学者，擅长撰写有说服力、有温度的前言，既能体现学术价值又能吸引目标读者。"
    style_block = _format_publisher_style(book)
    if style_block:
        user_content = user_content + "\n\n" + style_block

    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_content[:12000]},
    ]
    content = _call_llm_with_retry(session, task, messages, model, progress_hint="前言", stream_to_task=True)

    session.refresh(task)
    if task.status == TaskStatus.CANCELLED:
        return
    content = _normalize_paragraph_spacing(content)
    # 字数控制：前言目标 ~3000 中文字符
    task.current_output = content
    content = _enforce_target_cn_len(
        session,
        task,
        model,
        messages,
        content,
        target=3000,
        min_len=2800,
        max_len=3300,
        max_rounds=3,
        progress_hint="补足前言字数",
    )
    content = _normalize_paragraph_spacing(content)
    book.preface = content
    task.current_output = content


def _run_outline_l2(session: Session, task: GenerationTask, book: Book, model: str) -> None:
    """Level 2：章节标题 → 论证细纲。"""
    if not task.chapter_id:
        raise ValueError("outline_l2 任务缺少 chapter_id")
    chapter = session.get(Chapter, task.chapter_id)
    if not chapter:
        raise ValueError(f"Chapter id={task.chapter_id} 不存在")

    task.progress_message = f"生成细纲：{chapter.title}…"
    session.flush()

    outline_context = ""
    if book.outline:
        outline_context = (book.outline.content or "")[:PROMPT_OUTLINE_MAX]

    if _is_novel(book):
        user_content = f"""请为以下章节撰写**本章情节点/细纲**（Markdown），用于后续写正文。

**全书大纲摘要**：
{outline_context or "（无）"}

**本章标题**：{chapter.title}

要求：列出本章 3–6 个情节点或场景（谁做了什么、冲突/转折、可标注预期爽点或悬念），便于按网文节奏扩写。输出纯 Markdown。"""
        system_msg = "你是网文策划，负责将章节标题展开为情节点细纲，节奏清晰、便于写正文。"
    else:
        user_content = f"""请为以下章节撰写**论证细纲**（Markdown），用于后续扩写正文。

**全书大纲摘要**：
{outline_context or "（无）"}

**本章标题**：{chapter.title}

要求：按"提出主张 -> 论据 -> 论证 -> 总结/过渡"列出小节与要点，便于后续按学术规范扩写。输出纯 Markdown。"""
        if _is_bestseller_academic(book):
            system_msg = "你是畅销型知识书编辑，将章节标题展开为细纲时兼顾逻辑与可读性：每节有清晰问题或故事线，便于写出好读又有学术味的正文。"
        else:
            system_msg = "你是学术图书编辑，负责将章节标题展开为论证细纲，结构清晰、便于扩写。"
    style_block = _format_publisher_style(book)
    if style_block:
        user_content = user_content + "\n\n" + style_block

    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_content},
    ]
    outline_content = _call_llm_with_retry(session, task, messages, model, progress_hint="本章细纲", stream_to_task=True)
    outline_content = _normalize_paragraph_spacing(outline_content)
    chapter.outline_content = outline_content
    task.current_output = outline_content


def _parse_outline_to_sections(outline_text: str) -> list[dict[str, str]]:
    """将论证细纲拆为小节列表，便于按节生成。识别 ## / ###、1.1、一、结语 等。"""
    import re
    text = (outline_text or "").strip()
    if not text:
        return []
    lines = text.split("\n")
    sections: list[dict[str, str]] = []
    current_title: str | None = None
    current_body: list[str] = []

    def flush_section():
        if current_title is not None:
            body = "\n".join(current_body).strip()
            sections.append({"title": current_title, "body": body or current_title})

    # 小节标题模式：## 1.1 标题、### 1.1、1.1 标题、一、二、结语 / 小结 / 总结（独立成行或带 # 前缀）
    section_pattern = re.compile(
        r"^(#{1,3}\s*)?"
        r"(\d+(?:\.\d+)*\s*[^\s#]*|[一二三四五六七八九十]+[、．.]\s*[^\s#]*|结语|小结|总结|本章小结)\s*$"
    )
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if current_body:
                current_body.append("")
            continue
        match = section_pattern.match(stripped)
        if match:
            flush_section()
            current_title = stripped.lstrip("#").strip()
            current_body = []
        else:
            if current_title is None:
                current_title = "本节"
                current_body = []
            current_body.append(stripped)
    flush_section()
    return sections


def _run_academic_chapter_by_sections(
    session: Session,
    task: GenerationTask,
    book: Book,
    model: str,
    chapter: Chapter,
    glossary_block: str,
    reference_block: str,
    chapter_outline: str,
    sections: list[dict[str, str]],
    revision_instruction: str | None,
) -> str:
    """学术章节按小节分段生成，每节单次调用，避免单次 max_tokens 不足导致截断或质量下降。"""
    parts: list[str] = []
    for i, sec in enumerate(sections):
        task.progress_message = f"正在撰写：{chapter.title} — {sec['title']}…"
        # 将已完成部分持续写回，便于前端轮询时“看到正在输出”
        if parts:
            task.current_output = "\n\n".join(parts).strip()
        session.commit()
        session.refresh(task)
        if task.status == TaskStatus.CANCELLED:
            return "\n\n".join(parts)

        section_outline = (sec.get("body") or sec.get("title") or "").strip() or sec["title"]
        draft_ctx = (getattr(chapter, "draft_content", None) or "").strip()
        draft_block = ""
        if draft_ctx:
            draft_block = "\n\n[Chapter_Draft]（本章草稿，用于保持叙述一致性与衔接；不要逐字照抄，但要保持结构与口径一致）\n" + draft_ctx[:4500]
        user_content = f"""请根据以下 [Current_Chapter_Outline] 和 [Pre-defined_Glossary]、[Reference] **仅撰写本章中的这一小节**正文，严格遵循系统提示中的写作原则与输出规范。写作时要与本章草稿口径一致、与已完成小节衔接自然。{draft_block}

[Current_Chapter_Outline]（本章完整细纲，供上下文）
{chapter_outline[:3500]}

[Pre-defined_Glossary]
{glossary_block}

[Reference]
{reference_block}

本章标题：**{chapter.title}**

**当前小节**：**{sec['title']}**
本小节细纲要点：
{section_outline[:800]}

**输出格式（必须严格遵守）**：
1) 第一行必须是该小节标题：`## {sec['title']}`（标题文本必须完全一致）；
2) 随后空一行再写正文段落；
3) 只输出这一小节（包含标题行），不要输出其他小节内容或额外说明。

**字数要求**：本小节正文约 **3000 字**（中文字符，允许 2800–3300）。与前后小节衔接自然，本小节须写完整、勿在半途截断。"""
        if revision_instruction:
            user_content += f"\n\n【用户修改意图】{revision_instruction}"
        style_block = _format_publisher_style(book)
        if style_block:
            user_content += "\n\n" + style_block
        messages = [
            {"role": "system", "content": _get_academic_system_prompt(book)},
            {"role": "user", "content": user_content},
        ]
        section_text = _call_llm_with_retry(
            session,
            task,
            messages,
            model,
            max_tokens=5000,
            progress_hint=f"第 {i + 1}/{len(sections)} 节：{sec['title']}",
            stream_to_task=True,
        )
        if section_text:
            section_text = _normalize_paragraph_spacing(section_text.strip())
            # 每小节字数控制：目标 ~3000 中文字符
            task.current_output = section_text
            section_text = _enforce_target_cn_len(
                session,
                task,
                model,
                messages,
                section_text,
                target=3000,
                min_len=2800,
                max_len=3300,
                max_rounds=2,
                progress_hint=f"补足小节字数：{sec['title']}",
            )
            parts.append(_normalize_paragraph_spacing(section_text))
            # 每节完成后立即写回数据库，前端可在“当前任务”区域实时看到累积输出
            task.current_output = "\n\n".join(parts).strip()
            session.commit()
    return "\n\n".join(parts)


def _run_chapter_draft_3000(
    session: Session,
    task: GenerationTask,
    book: Book,
    model: str,
    chapter: Chapter,
    glossary_block: str,
    reference_block: str,
    revision_instruction: str | None,
) -> str:
    """章节草稿：围绕本章 outline_fragment/outline_content，生成约 3000 中文字符的草稿。"""
    task.progress_message = f"生成章节草稿：{chapter.title}…"
    session.commit()
    session.refresh(task)
    if task.status == TaskStatus.CANCELLED:
        return ""

    chapter_scope_outline = (getattr(chapter, "outline_fragment", None) or "").strip() or (chapter.outline_content or "").strip()
    if not chapter_scope_outline:
        chapter_scope_outline = f"## 第 {chapter.order_index} 章 {chapter.title}\n-（未提供细纲，请先生成或编辑细纲）"

    if _is_novel(book):
        system_msg = FICTION_SYSTEM_PROMPT
        user_content = f"""请根据以下本章要点，撰写本章**草稿**（约 3000 字，中文字符，允许 2800–3300）。

本章标题：**{chapter.title}**

[Current_Plot_Outline]（本章要点）
{chapter_scope_outline[:4000]}

要求：
1) 输出 Markdown 分段正文；
2) 只写草稿，不要求全章超长；
3) 不要随意更换人名称呼或引入未提及设定。"""
    else:
        system_msg = _get_academic_system_prompt(book)
        # 权威结构信息：避免“全书共5章”这类事实错误。若数据库已有章节列表，则将其作为唯一可信来源。
        book_structure = _format_book_structure(session, book.id)
        user_content = f"""请根据以下信息撰写本章**章节草稿**（约 3000 字，中文字符，允许 2800–3300），用于后续逐节终稿。

本章标题：**{chapter.title}**

[Book_Structure]（权威信息：若需描述“全书结构/章节数”，必须严格以此为准；不得自行臆造章数）
{book_structure}

[Current_Chapter_Outline]（仅本章范围，不要扩展到其他章节）
{chapter_scope_outline[:4000]}

[Pre-defined_Glossary]
{glossary_block}

[Reference]
{reference_block}

要求：
1) 输出 Markdown 正文，**必须按二级小节展开**：若细纲中已有 2–6 个小节，请逐一写出对应小节（用「一、二、三、…」），每个小节下至少再拆 2 个三级标题（用「（一）（二）…」），以实现“向下扩写”而非重复横向小节；
2) 必须贴合本章要点，不要引入与本章无关的新主题；
3) **禁止重复**：不得为了凑字数重复输出同名小节或复述同一段落；需要补充时只在既有小节内部增加更细层级标题或补充论证/案例/过渡；
4) 术语严格按术语表，不做同义替换。"""
    if revision_instruction:
        user_content += "\n\n【用户修改意图】" + revision_instruction
    style_block = _format_publisher_style(book)
    if style_block:
        user_content += "\n\n" + style_block

    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_content},
    ]
    draft = _call_llm_with_retry(session, task, messages, model, max_tokens=6000, progress_hint="章节草稿", stream_to_task=True)
    session.refresh(task)
    if task.status == TaskStatus.CANCELLED:
        return ""
    draft = _normalize_paragraph_spacing(draft)
    task.current_output = draft
    draft = _enforce_target_cn_len(
        session,
        task,
        model,
        messages,
        draft,
        target=3000,
        min_len=2800,
        max_len=3300,
        max_rounds=2,
        progress_hint="补足章节草稿字数",
    )
    return _normalize_paragraph_spacing(draft)


def _format_book_structure(session: Session, book_id: int) -> str:
    """以数据库为准输出全书章节结构（章数与标题）。"""
    try:
        chapters = session.execute(
            select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.order_index)
        ).scalars().all()
    except Exception:
        chapters = []
    if not chapters:
        return "（暂无章节列表；如需描述全书结构，请使用谨慎表述，避免断言具体章数）"
    lines = [f"本书共 {len(chapters)} 章："]
    for ch in chapters[:50]:
        lines.append(f"- 第 {ch.order_index} 章：{ch.title}")
    return "\n".join(lines)


def _run_chapter_section_finalize_3000(
    session: Session,
    task: GenerationTask,
    book: Book,
    model: str,
    chapter: Chapter,
    glossary_block: str,
    reference_block: str,
    chapter_outline: str,
    revision_instruction: str | None,
) -> str:
    """逐节终稿：按 outline 解析小节，每小节约 3000 字，最后拼接成整章。"""
    sections = _parse_outline_to_sections(chapter_outline)
    if not sections:
        sections = [{"title": chapter.title, "body": chapter_outline}]
    task.current_output = f"# {chapter.title}\n\n"
    session.commit()
    # 可选：只生成某一小节（由 task.params.section_title 指定）
    only_title = None
    if task.params and isinstance(task.params, dict) and task.params.get("section_title"):
        only_title = str(task.params.get("section_title") or "").strip() or None
    if only_title:
        picked = next((s for s in sections if (s.get("title") or "").strip() == only_title), None)
        if not picked:
            picked = {"title": only_title, "body": only_title}
        return _run_academic_chapter_by_sections(
            session,
            task,
            book,
            model,
            chapter,
            glossary_block,
            reference_block,
            chapter_outline,
            [picked],
            revision_instruction,
        )
    return _run_academic_chapter_by_sections(
        session,
        task,
        book,
        model,
        chapter,
        glossary_block,
        reference_block,
        chapter_outline,
        sections,
        revision_instruction,
    )


def _run_chapter(
    session: Session,
    task: GenerationTask,
    book: Book,
    model: str,
    glossary_terms: list[dict[str, Any]],
) -> None:
    """Level 3 / 重写：细纲 + 术语表 + 参考文献 → 章节正文。学术专著支持按小节分段生成以缓解 max_tokens 上限。"""
    if not task.chapter_id:
        raise ValueError("chapter/rewrite 任务缺少 chapter_id")
    chapter = session.get(Chapter, task.chapter_id)
    if not chapter:
        raise ValueError(f"Chapter id={task.chapter_id} 不存在")

    task.progress_message = f"正在撰写：{chapter.title}…"
    session.commit()

    glossary_block = _format_glossary(glossary_terms)
    reference_block = _format_references(session, book.id)
    chapter_outline = (chapter.outline_content or "").strip() or "（无细纲）"
    stage = None
    if task.params and isinstance(task.params, dict):
        stage = (str(task.params.get("stage") or "").strip().lower() or None)

    if _is_novel(book):
        character_block = _format_character_cards(glossary_terms)
        world_setting = (book.core_concept or "").strip() or "（见全书大纲）"
        user_content = f"""请根据以下 [Character_Cards] 与 [Current_Plot_Outline] 撰写一章网络小说正文，严格遵循系统提示中的小说写作原则（Show Don't Tell、断章悬念、人设维持）。

[World_Setting]（世界观/故事背景）
{world_setting[:1500]}

[Character_Cards]（人物卡；写作时须保持人设一致，勿崩人设、勿擅自改名或换称呼）
{character_block}

[Current_Plot_Outline]（本章细纲）
{chapter_outline[:4000]}

本章标题：**{chapter.title}**

**字数要求**：本章正文 **2000–4000 字**（中文字符）。章末须留断章悬念（Cliffhanger）。
**风格要求（必须满足）**：具有强烈的画面感（Show Don't Tell，用具体动作与环境代替抽象形容）与对话感（多用人物对话推进剧情，少用大段旁白）。"""
        revision = ""
        if task.params and isinstance(task.params, dict) and task.params.get("revision_instruction"):
            revision = "\n\n【用户修改意图】" + (str(task.params["revision_instruction"]).strip() or "")
        user_content += revision
        style_block = _format_publisher_style(book)
        if style_block:
            user_content += "\n\n" + style_block
        messages = [
            {"role": "system", "content": FICTION_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
        if stage == "chapter_draft":
            rev = None
            if task.params and isinstance(task.params, dict) and task.params.get("revision_instruction"):
                rev = str(task.params.get("revision_instruction") or "").strip() or None
            content = _run_chapter_draft_3000(session, task, book, model, chapter, glossary_block, reference_block, rev)
        else:
            content = _call_llm_with_retry(session, task, messages, model, max_tokens=8000, progress_hint="本章正文", stream_to_task=True)
        session.refresh(task)
        if task.status == TaskStatus.CANCELLED:
            return
        min_chapter_chars = 2000
    else:
        revision_instruction = None
        if task.params and isinstance(task.params, dict) and task.params.get("revision_instruction"):
            revision_instruction = str(task.params["revision_instruction"]).strip() or None
        if stage == "chapter_draft":
            content = _run_chapter_draft_3000(session, task, book, model, chapter, glossary_block, reference_block, revision_instruction)
            session.refresh(task)
            if task.status == TaskStatus.CANCELLED:
                return
            min_chapter_chars = 2800
        elif stage == "section_finalize":
            content = _run_chapter_section_finalize_3000(
                session,
                task,
                book,
                model,
                chapter,
                glossary_block,
                reference_block,
                chapter_outline,
                revision_instruction,
            )
            session.refresh(task)
            if task.status == TaskStatus.CANCELLED:
                return
            min_chapter_chars = 2800
        else:
            sections = _parse_outline_to_sections(chapter_outline)
            if len(sections) >= 2:
                # 分段生成时，先写入一个占位，便于前端立刻显示“开始输出”
                task.current_output = f"# {chapter.title}\n\n"
                session.commit()
                content = _run_academic_chapter_by_sections(
                    session, task, book, model, chapter,
                    glossary_block, reference_block, chapter_outline,
                    sections, revision_instruction,
                )
                session.refresh(task)
                if task.status == TaskStatus.CANCELLED:
                    return
                min_chapter_chars = 6000
            else:
                user_content = f"""请根据以下 [Current_Chapter_Outline] 和 [Pre-defined_Glossary]、[Reference] 扩写本章正文，严格遵循系统提示中的写作原则与输出规范。

[Current_Chapter_Outline]
{chapter_outline[:4000]}

[Pre-defined_Glossary]
{glossary_block}

[Reference]
{reference_block}

本章标题：**{chapter.title}**

**字数要求（必须满足）**：本章正文不少于 **6000 字**、不超过 **12000 字**（按中文字符计）。请按细纲逐节充分展开至整章完整收束，保证论证完整、层次分明；勿在半途结束。禁止敷衍或堆砌。

请直接输出本章正文（Markdown），不要输出"本章正文如下"等前缀。"""
                revision = ""
                if revision_instruction:
                    revision = "\n\n【用户明确要求，必须优先满足】\n用户对本章的修改意图：{}\n请严格按上述意图调整正文。".format(revision_instruction)
                user_content += revision
                style_block = _format_publisher_style(book)
                if style_block:
                    user_content += "\n\n" + style_block
                messages = [
                    {"role": "system", "content": _get_academic_system_prompt(book)},
                    {"role": "user", "content": user_content},
                ]
                content = _call_llm_with_retry(
                    session,
                    task,
                    messages,
                    model,
                    max_tokens=8192,
                    progress_hint="本章正文",
                    stream_to_task=True,
                )
                session.refresh(task)
                if task.status == TaskStatus.CANCELLED:
                    return
                min_chapter_chars = 6000

    # 学术专著不足 6000 字时二次扩写；网文不足 2000 字时也可补足
    if content and len(content) < min_chapter_chars and stage not in ("chapter_draft", "section_finalize"):
        task.progress_message = "正文偏短，正在扩写补足…"
        session.commit()
        session.refresh(task)
        if task.status == TaskStatus.CANCELLED:
            return
        content = _expand_short_chapter(session, task, book, model, content, chapter, glossary_terms, min_chapter_chars)

    content = _normalize_paragraph_spacing(content)
    if stage == "chapter_draft":
        # 草稿独立存储，避免覆盖终稿，便于后续逐节终稿/对照修改
        chapter.draft_content = content
        # 不改变终稿 content；但仍把输出写到 task.current_output 方便前端实时显示
        task.current_output = content
        chapter.status = ChapterStatus.DRAFT.value
    else:
        # 若是“只生成某一小节”，则把该小节写入终稿正文对应位置（优先以草稿为底稿）
        only_sec = None
        if stage == "section_finalize" and task.params and isinstance(task.params, dict) and task.params.get("section_title"):
            only_sec = str(task.params.get("section_title") or "").strip() or None
        if only_sec:
            base = (chapter.content or "").strip() or (getattr(chapter, "draft_content", None) or "").strip()
            # 确保 content 包含标题行；若没有则补上
            sec_text = content
            if sec_text and not re.match(r"^#{2,3}\s+", sec_text.strip().splitlines()[0] if sec_text.strip().splitlines() else ""):
                sec_text = "## " + only_sec + "\n\n" + sec_text
            try:
                before, _, after = _split_markdown_section_by_title(base, only_sec) if base else ("", "", "")
                merged = "\n\n".join([p for p in [before, sec_text, after] if p and p.strip()]).strip()
            except Exception:
                merged = ("\n\n".join([base, sec_text]).strip() if base else sec_text)
            chapter.content = merged
        else:
            chapter.content = content
        if stage == "section_finalize":
            chapter.status = ChapterStatus.FINAL.value
        else:
            chapter.status = ChapterStatus.DRAFT.value
        chapter.word_count = len(chapter.content or "")
        task.current_output = chapter.content or content

        # 章节终稿/分节终稿：同步落库引用关系（从正文中的 [Source_ID] 解析）
        if not _is_novel(book):
            try:
                _sync_citations_from_text(session, chapter.id, chapter.content or "")
            except Exception as e:
                logger.warning("sync citations failed: %s", e)


def _split_markdown_section_by_title(text: str, title: str) -> tuple[str, str, str]:
    """
    以标题定位一个小节（## 或 ###），返回 (before, section_with_heading, after)。
    - title 允许传入不带 # 的纯标题文本
    - 若找不到则抛 ValueError
    """
    if not text or not title:
        raise ValueError("空文本或空标题无法定位小节")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    t = title.strip().lstrip("#").strip()
    start_idx = None
    start_level = None
    for i, line in enumerate(lines):
        m = re.match(r"^(#{2,3})\s+(.*)$", line.strip())
        if not m:
            continue
        lvl = len(m.group(1))
        name = (m.group(2) or "").strip()
        if name == t:
            start_idx = i
            start_level = lvl
            break
    if start_idx is None or start_level is None:
        raise ValueError(f"未找到标题为「{t}」的小节（仅支持 ##/### 标题）")
    end_idx = len(lines)
    for j in range(start_idx + 1, len(lines)):
        m2 = re.match(r"^(#{2,3})\s+(.*)$", lines[j].strip())
        if not m2:
            continue
        lvl2 = len(m2.group(1))
        if lvl2 <= start_level:
            end_idx = j
            break
    before = "\n".join(lines[:start_idx]).rstrip()
    section = "\n".join(lines[start_idx:end_idx]).strip()
    after = "\n".join(lines[end_idx:]).lstrip()
    return before, section, after


def _sync_citations_from_text(session: Session, chapter_id: int, text: str) -> None:
    """
    从正文中解析引用标注 [123] 并同步到 citations 表。
    - 仅解析数字方括号，避免误把 [Reference] 等当作引用
    - 会先清空该章旧 citations，再写入新引用（去重）
    """
    if not chapter_id:
        return
    src = (text or "").strip()
    # 清空旧引用
    session.query(Citation).filter(Citation.chapter_id == chapter_id).delete()
    if not src:
        session.flush()
        return
    ids = []
    seen = set()
    for m in re.finditer(r"\[(\d{1,8})\]", src):
        rid = int(m.group(1))
        if rid <= 0 or rid in seen:
            continue
        seen.add(rid)
        ids.append(rid)
    if not ids:
        session.flush()
        return
    # 校验 reference 是否存在（避免插入无效外键）
    existing = set(session.execute(select(Reference.id).where(Reference.id.in_(ids))).scalars().all())
    for rid in ids:
        if rid not in existing:
            continue
        session.add(Citation(chapter_id=chapter_id, reference_id=rid, location_in_text=None, snippet=None))
    session.flush()


def _split_text_by_anchors(text: str, anchor_start: str, anchor_end: str) -> tuple[str, str, str]:
    """用起止片段定位一个范围，返回 (before, middle, after)。"""
    if not text:
        raise ValueError("空文本无法定位段落范围")
    s = text
    a = (anchor_start or "").strip()
    b = (anchor_end or "").strip()
    if not a or not b:
        raise ValueError("anchor_start/anchor_end 不能为空")
    i = s.find(a)
    if i < 0:
        raise ValueError("未找到 anchor_start")
    j = s.find(b, i + len(a))
    if j < 0:
        raise ValueError("未找到 anchor_end")
    j2 = j + len(b)
    return s[:i].rstrip(), s[i:j2].strip(), s[j2:].lstrip()


def _run_rewrite(
    session: Session,
    task: GenerationTask,
    book: Book,
    model: str,
    glossary_terms: list[dict[str, Any]],
) -> None:
    """按 scope 定向重写：只改指定片段，不改其他内容。"""
    if not task.chapter_id:
        raise ValueError("rewrite 任务缺少 chapter_id")
    chapter = session.get(Chapter, task.chapter_id)
    if not chapter:
        raise ValueError(f"Chapter id={task.chapter_id} 不存在")

    params = task.params if isinstance(task.params, dict) else {}
    scope = (params.get("scope") or "").strip()
    instruction = (params.get("instruction") or params.get("revision_instruction") or "").strip()
    if not scope:
        raise ValueError("rewrite 缺少 scope")
    if not instruction:
        raise ValueError("rewrite 缺少 instruction")

    glossary_block = _format_glossary(glossary_terms)
    reference_block = _format_references(session, book.id)

    task.progress_message = "正在定向修改…"
    session.commit()
    session.refresh(task)
    if task.status == TaskStatus.CANCELLED:
        return

    if scope == "outline_chapter_fragment":
        frag = (getattr(chapter, "outline_fragment", None) or "").strip()
        if not frag:
            raise ValueError("本章 outline_fragment 为空，无法定向修改大纲片段")
        user_content = f"""请根据【用户修改意图】仅修改下面这一段「本章大纲片段」，不改动未提及信息，不新增其他章节内容。

【用户修改意图】
{instruction}

【本章大纲片段】
{frag}

要求：输出修改后的「本章大纲片段」全文（Markdown）。"""
        messages = [
            {"role": "system", "content": "你是严谨的图书策划编辑，只修改被要求的段落，未提及部分保持不变。"},
            {"role": "user", "content": user_content},
        ]
        new_frag = _call_llm_with_retry(session, task, messages, model, max_tokens=2500, progress_hint="定向修改大纲片段", stream_to_task=True)
        session.refresh(task)
        if task.status == TaskStatus.CANCELLED:
            return
        new_frag = _normalize_paragraph_spacing(new_frag)
        chapter.outline_fragment = new_frag
        # 同步重建 outline.content（若存在 Outline.intro）
        if book.outline:
            chapters_sorted = sorted(session.execute(select(Chapter).where(Chapter.book_id == book.id).order_by(Chapter.order_index)).scalars().all(), key=lambda c: c.order_index)
            intro = getattr(book.outline, "intro", None) or ""
            book.outline.content = _build_outline_content(intro, chapters_sorted) or (book.outline.content or "")
        task.current_output = new_frag
        return

    target = (params.get("target") or "").strip().lower() or "final"
    if target not in ("final", "draft"):
        target = "final"
    content = ((chapter.draft_content if target == "draft" else chapter.content) or "").strip()
    if not content:
        raise ValueError("目标文本为空，无法定向修改；请先生成正文或草稿")

    if scope == "chapter_section":
        section_title = (params.get("section_title") or "").strip()
        before, section, after = _split_markdown_section_by_title(content, section_title)
        user_content = f"""请仅重写下面这一小节（包含标题行），满足【用户修改意图】。不要改动其他小节；不要改变小节标题文本；术语遵守术语表。

【用户修改意图】
{instruction}

[Pre-defined_Glossary]
{glossary_block}

[Reference]
{reference_block}

【要重写的小节】
{section}

要求：只输出“重写后的小节”（包含原小节标题行），不要输出其他内容。"""
        system_msg = _get_academic_system_prompt(book) if not _is_novel(book) else FICTION_SYSTEM_PROMPT
        messages = [{"role": "system", "content": system_msg}, {"role": "user", "content": user_content}]
        new_section = _call_llm_with_retry(session, task, messages, model, max_tokens=5000, progress_hint="定向重写小节", stream_to_task=True)
        session.refresh(task)
        if task.status == TaskStatus.CANCELLED:
            return
        new_section = _normalize_paragraph_spacing(new_section)
        stitched = "\n\n".join([p for p in [before, new_section, after] if p and p.strip()]).strip()
        if target == "draft":
            chapter.draft_content = stitched
        else:
            chapter.content = stitched
            chapter.word_count = len(stitched)
        task.current_output = new_section
        return

    if scope == "chapter_paragraph_range":
        a = (params.get("anchor_start") or "").strip()
        b = (params.get("anchor_end") or "").strip()
        before, middle, after = _split_text_by_anchors(content, a, b)
        user_content = f"""请仅重写下面这一段文本范围（保持其在全文中的位置不变），满足【用户修改意图】。不要改动其他段落；保持上下文衔接自然。

【用户修改意图】
{instruction}

[Pre-defined_Glossary]
{glossary_block}

[Reference]
{reference_block}

【要重写的范围】
{middle}

要求：只输出“重写后的范围文本”，不要输出其他内容。"""
        system_msg = _get_academic_system_prompt(book) if not _is_novel(book) else FICTION_SYSTEM_PROMPT
        messages = [{"role": "system", "content": system_msg}, {"role": "user", "content": user_content}]
        new_mid = _call_llm_with_retry(session, task, messages, model, max_tokens=3000, progress_hint="定向重写段落", stream_to_task=True)
        session.refresh(task)
        if task.status == TaskStatus.CANCELLED:
            return
        new_mid = _normalize_paragraph_spacing(new_mid)
        stitched = "\n\n".join([p for p in [before, new_mid, after] if p and p.strip()]).strip()
        if target == "draft":
            chapter.draft_content = stitched
        else:
            chapter.content = stitched
            chapter.word_count = len(stitched)
        task.current_output = new_mid
        return

    raise ValueError(f"不支持的 rewrite scope: {scope}")


def _expand_short_chapter(
    session: Session,
    task: GenerationTask,
    book: Book,
    model: str,
    content: str,
    chapter: Chapter,
    glossary_terms: list[dict[str, Any]],
    min_chars: int,
) -> str:
    """章节正文不足 min_chars 时，调用 LLM 扩写补足。学术模式：扩充论证与案例；小说模式：扩充对话、动作与内心戏，严禁学术化表述。"""
    if _is_novel(book):
        character_block = _format_character_cards(glossary_terms)
        char_hint = ""
        if character_block and "（暂无具体角色设定）" not in character_block:
            char_hint = "\n\n[人物卡]（扩写时人名与人设须与以下一致）\n" + character_block[:2000] + "\n\n"
        user_content = f"""以下为本章当前正文，字数不足 {min_chars} 字。请**仅用小说手法**补足篇幅：
- 增加人物对话、具体动作与神态描写、内心独白或环境氛围；
- 保持原有剧情顺序与角色人名/人设一致，章末保留悬念感；
- **禁止**加入论证、案例分析、术语解释或学术化表述。
使全文达到至少 {min_chars} 字，只输出完整章节正文，不要输出任何说明。{char_hint}---\n当前正文：\n{content[:10000]}"""
        system_msg = "你是网文写手，只通过对话、动作、心理与氛围描写扩充篇幅，严禁使用学术或论文式表述。"
        max_tok = 8000
    else:
        glossary_block = _format_glossary(glossary_terms)
        user_content = f"""以下为本章当前正文，字数不足 {min_chars} 字。请在不改变原有小节标题与论点顺序的前提下，对适当段落进行扩充（增加论证、案例、过渡或细化表述），使全文达到至少 {min_chars} 字。保持 Markdown 格式与学术语气，只输出完整章节正文，不要输出任何说明或注释。

[Pre-defined_Glossary]
{glossary_block[:2000]}

---

当前正文：
{content[:12000]}
"""
        system_msg = "你是学术图书编辑，负责在保持原文结构的前提下扩充篇幅，使论证更充分。"
        max_tok = 12000
    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_content},
    ]
    expanded = _call_llm_with_retry(session, task, messages, model, max_tokens=min(max_tok, 8192), progress_hint="扩写补足", stream_to_task=True)
    # 若扩写后仍偏短或扩写失败，仍返回扩写结果
    if expanded and len(expanded) > len(content):
        return _normalize_paragraph_spacing(expanded)
    return content


def _format_glossary(terms: list[dict[str, Any]]) -> str:
    """将术语列表格式化为 [Glossary] 块（学术用：术语-定义）。"""
    if not terms:
        return "（暂无术语表）"
    lines = []
    for t in terms:
        term = t.get("term") or t.get("term_")
        if not term:
            continue
        definition = t.get("definition") or ""
        lines.append(f"- **{term}**：{definition}" if definition else f"- **{term}**")
    text = "\n".join(lines)[:PROMPT_GLOSSARY_MAX]
    return text or "（暂无）"


def _format_character_cards(terms: list[dict[str, Any]]) -> str:
    """小说模式专用：将 Term 表的人设/伏笔格式化为人物卡（档案感，避免学术字典感）。"""
    if not terms:
        return "（暂无具体角色设定，请根据大纲自行发挥并保持逻辑自洽）"
    lines = ["## 核心人物卡 (保持人设一致性)"]
    for t in terms:
        name = (t.get("term") or t.get("term_") or "").strip() or "未命名角色"
        desc = (t.get("definition") or "").strip() or "性格待定"
        lines.append(f"### 姓名：{name}\n- **性格与特征**：{desc}\n- **行为逻辑**：必须符合上述设定，严禁崩人设。")
    return "\n\n".join(lines)[:PROMPT_GLOSSARY_MAX]


def _format_references(session: Session, book_id: int) -> str:
    """将本书参考文献格式化为 [Reference] 块。"""
    refs = session.execute(select(Reference).where(Reference.book_id == book_id)).scalars().all()
    if not refs:
        return "（暂无参考文献，论述时请使用学术推测性表述并注明「待补充文献」）"
    lines = []
    for r in refs:
        snippet = (r.content_extract or r.title or r.citation_key or "")[:200]
        lines.append(f"- **[{r.id}]** {r.citation_key}：{snippet}")
    return "\n".join(lines)[:PROMPT_REFERENCE_MAX]


def _run_audit(session: Session, task: GenerationTask, book: Book, model: str) -> None:
    """审计：学术模式检查术语/论证/口语化；小说模式检查人物一致性（人设、人名、称呼）。"""
    if task.chapter_id:
        chapter = session.get(Chapter, task.chapter_id)
        if chapter and chapter.content:
            task.progress_message = "审计本章…"
            session.flush()
            if _is_novel(book):
                rows = session.execute(select(Term).where(Term.book_id == book.id).order_by(Term.id)).all()
                terms_list = [r[0] for r in rows] if rows else []
                character_block = _format_character_cards([{"term": t.term, "definition": t.definition or ""} for t in terms_list])
                user_content = f"""请对以下小说章节做**人物一致性检查**（3–5 条）：1) 本章出现的人名、称呼是否与全书已有人物卡一致，有无中途改名或混用别称；2) 主要角色的行为、语气是否符合其人设；3) 是否有与上文设定矛盾的情节或状态。仅输出审计要点，每条一行。

[已有角色/人物卡]
{character_block[:2000]}

---
本章正文（节选）：
{chapter.content[:3000]}"""
                messages = [
                    {"role": "system", "content": "你是网文审稿人，只做人物一致性审计，只输出要点，不修改正文。"},
                    {"role": "user", "content": user_content},
                ]
            else:
                user_content = f"""请对以下章节做简短学术审计（3–5 条）：1) 是否与术语表一致；2) 论证是否闭环；3) 是否存在口语化表述。仅输出要点，每条一行。\n\n---\n{chapter.content[:3000]}"""
                messages = [
                    {"role": "system", "content": "你是学术审稿人，只输出审计要点，不修改正文。"},
                    {"role": "user", "content": user_content},
                ]
            try:
                audit_note = _call_llm_with_retry(session, task, messages, model, progress_hint="审计中", stream_to_task=True)
                task.current_output = "审计完成\n\n" + audit_note
            except Exception as e:
                logger.warning("审计 LLM 调用失败，仅更新状态: %s", e)
                task.current_output = "审计完成（模型调用未执行）"
        if chapter:
            chapter.status = ChapterStatus.AUDITED.value
    else:
        task.current_output = "审计完成（占位）"
