# -*- coding: utf-8 -*-
"""
自动化写书 — Streamlit 入口
侧边栏选书/新建、API 连接测试；主区为控制台（任务监控、流式正文）、大纲、章节、知识中枢。
"""
import time
from datetime import datetime, timedelta

import streamlit as st
from sqlalchemy import select

from app.config import API_TEST_PROVIDERS, DEFAULT_MODELS
from app.database import init_db, get_session
from app.models import (
    Book,
    BookStatus,
    Chapter,
    Conflict,
    ConflictStatus,
    GenerationTask,
    Outline,
    Reference,
    TaskStatus,
    TaskType,
    Term,
)

# ---------- 页面配置 ----------
st.set_page_config(
    page_title="自动化写书",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------- 自定义 CSS（书卷气无衬线体、侧边栏、按钮、信息框、主题色、宽屏开关） ----------
def _inject_custom_css(theme: str = "academic", wide_mode: bool = True):
    theme_accent = "#1e3a5f" if theme == "academic" else "#c45c26"
    theme_bg = "rgba(30, 58, 95, 0.06)" if theme == "academic" else "rgba(196, 92, 38, 0.06)"
    st.markdown(
        f"""
        <style>
        /* 全局字体：书卷气无衬线 */
        @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@300;400;500;600;700&display=swap');
        html, body, [class*="css"] {{
            font-family: 'Noto Sans SC', 'PingFang SC', 'Microsoft YaHei', sans-serif !important;
        }}
        /* 侧边栏浅灰背景与按钮圆角悬停 */
        [data-testid="stSidebar"] {{
            background: linear-gradient(180deg, #f0f0f0 0%, #e8e8e8 100%) !important;
        }}
        [data-testid="stSidebar"] .stButton > button {{
            border-radius: 8px !important;
            transition: box-shadow 0.2s, transform 0.1s !important;
        }}
        [data-testid="stSidebar"] .stButton > button:hover {{
            box-shadow: 0 4px 12px rgba(0,0,0,0.12) !important;
        }}
        /* 主区按钮同样圆角与悬停 */
        .stButton > button {{
            border-radius: 8px !important;
            transition: box-shadow 0.2s !important;
        }}
        .stButton > button:hover {{
            box-shadow: 0 4px 14px rgba(0,0,0,0.15) !important;
        }}
        /* 美化 st.info / st.status 等提示框 */
        [data-testid="stAlert"] {{
            border-radius: 10px !important;
            border-left: 4px solid {theme_accent} !important;
            background: {theme_bg} !important;
        }}
        div[data-baseweb="notification"] {{
            border-radius: 10px !important;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08) !important;
        }}
        /* 主题色变量（供后续扩展） */
        :root {{
            --accent: {theme_accent};
            --accent-bg: {theme_bg};
        }}
        """
        + (
            """
        /* 宽屏模式关闭时约束主内容宽度 */
        .main .block-container {
            max-width: 960px !important;
            margin-left: auto !important;
            margin-right: auto !important;
        }
        """
            if not wide_mode
            else ""
        )
        + """
        </style>
        """,
        unsafe_allow_html=True,
    )


# ---------- 初始化数据库 ----------
@st.cache_resource
def ensure_db():
    init_db()
    return True


ensure_db()

# ---------- 侧边栏：项目与全局 ----------
def render_sidebar():
    with st.sidebar:
        st.title("📚 自动化写书")
        st.caption("创作控制台")
        st.divider()

        session = get_session()
        try:
            books = session.execute(select(Book).order_by(Book.updated_at.desc())).scalars().all()
        finally:
            session.close()

        book_options = ["+ 新建书籍项目"] + [f"{b.title} (ID:{b.id})" for b in books]
        selected = st.selectbox("选择项目", book_options, key="sidebar_book")

        if selected == "+ 新建书籍项目":
            with st.form("new_book_form"):
                new_title = st.text_input("书名", placeholder="例：《量子纠缠在分布式计算中的应用》或《某某修仙传》")
                new_book_type = st.selectbox("书籍类型", ["学术", "小说"], key="new_book_type")
                new_concept = st.text_area("核心构思 / 研究假设", height=100, placeholder="学术：核心观点与论证思路；小说：主角设定、世界观、主线冲突或开篇梗概")
                if st.form_submit_button("创建项目"):
                    if new_title.strip():
                        sess = get_session()
                        try:
                            content_type = "novel" if new_book_type == "小说" else "academic"
                            book = Book(
                                title=new_title.strip(),
                                core_concept=new_concept.strip() or None,
                                content_type=content_type,
                                book_type=content_type,
                            )
                            sess.add(book)
                            sess.commit()
                            st.success("已创建「{}」（{}）".format(book.title, "小说" if content_type == "novel" else "学术"))
                            st.rerun()
                        finally:
                            sess.close()
                    else:
                        st.warning("请输入书名")
            current_book = None
        else:
            # 解析当前选中的书 ID
            try:
                bid = int(selected.split("(ID:")[1].rstrip(")"))
                current_book = next((b for b in books if b.id == bid), None)
            except (IndexError, ValueError):
                current_book = books[0] if books else None

        # ---------- 书籍类型（学术/小说），保存到 Book.book_type 与 content_type ----------
        if current_book:
            st.divider()
            st.subheader("书籍类型")
            ct = getattr(current_book, "content_type", None) or getattr(current_book, "book_type", None) or "academic"
            book_type_index = 1 if (ct and ct.strip().lower() == "novel") else 0
            book_type_choice = st.selectbox(
                "选择书籍类型",
                ["学术", "小说"],
                index=book_type_index,
                key="sidebar_book_type",
                help="学术：论证一致、严谨客观、术语与证据闭环。小说：角色一致、画面感与对话感、伏笔与人物状态。",
            )
            if st.button("更新项目设定", key="btn_update_project_type"):
                sess = get_session()
                try:
                    b = sess.get(Book, current_book.id)
                    if b:
                        new_ct = "novel" if book_type_choice == "小说" else "academic"
                        if hasattr(b, "content_type"):
                            b.content_type = new_ct
                        if hasattr(b, "book_type"):
                            b.book_type = new_ct
                        sess.commit()
                        st.success("已保存：当前项目为「{}」".format(book_type_choice))
                        st.rerun()
                finally:
                    sess.close()

        st.divider()
        st.subheader("全局参数")
        default_model = st.selectbox(
            "默认模型",
            DEFAULT_MODELS,
            key="global_model",
        )
        tone = st.radio("学术语气", ["严谨", "通俗", "批判性"], key="global_tone", horizontal=True)
        if current_book:
            sess = get_session()
            try:
                b = sess.get(Book, current_book.id)
                if b:
                    b.default_model = default_model
                    b.tone = tone
                    sess.commit()
            finally:
                sess.close()

        st.divider()
        st.subheader("API 连接测试")
        api_provider = st.selectbox("选择提供商", list(API_TEST_PROVIDERS.keys()), key="api_test_provider")
        if st.button("测试连接", key="api_test_btn"):
            model = API_TEST_PROVIDERS.get(api_provider)
            if model:
                with st.spinner("正在连接 {}…".format(api_provider)):
                    err = _test_api_connection(model)
                    if err:
                        st.error("连接失败：{}".format(err))
                    else:
                        st.success("{} 连接成功".format(api_provider))

        st.divider()
        st.subheader("显示")
        st.toggle("宽屏模式", value=st.session_state.get("wide_mode", True), key="wide_mode", help="开启后正文预览区域使用全宽布局")
        st.divider()
        return current_book


# 控制台轮询间隔（秒），用于流式展示 Worker 正在写的文字
CONSOLE_REFRESH_INTERVAL = 3


def _monitoring_fragment_impl() -> None:
    """从 SQLite 查询当前书的 FAILED/RUNNING 任务并展示错误与监控卡片（供 fragment 或直接调用）。"""
    bid = st.session_state.get("_monitor_book_id")
    if not bid:
        return
    session = get_session()
    try:
        b = session.get(Book, bid)
        if not b:
            return
        failed = list(
            session.execute(
                select(GenerationTask).where(
                    GenerationTask.book_id == bid,
                    GenerationTask.status == TaskStatus.FAILED,
                ).order_by(GenerationTask.id.desc()).limit(1)
            ).scalars().all()
        )
        if failed:
            st.error("**Worker 报错**：{}".format(failed[0].error_message or "未知错误"))
        running = list(
            session.execute(
                select(GenerationTask).where(
                    GenerationTask.book_id == bid,
                    GenerationTask.status == TaskStatus.RUNNING,
                ).order_by(GenerationTask.started_at.desc())
            ).scalars().all()
        )
        _render_monitoring_card(b, running)
    finally:
        session.close()


def _book_kpis(session, book) -> tuple:
    """返回 (已写字数, 逻辑健康度 0–100, 累计耗时字符串)。"""
    word_count = 0
    for ch in book.chapters:
        if ch.content:
            word_count += len((ch.content or "").replace(" ", "").replace("\n", ""))
        elif getattr(ch, "word_count", None):
            word_count += ch.word_count or 0
    pending = session.execute(
        select(Conflict).where(
            Conflict.book_id == book.id,
            Conflict.resolution == ConflictStatus.PENDING,
        )
    ).scalars().all()
    health = max(0, 100 - len(pending) * 15)
    total_sec = 0
    for task in book.tasks:
        if getattr(task, "started_at", None) and getattr(task, "completed_at", None):
            try:
                total_sec += int((task.completed_at - task.started_at).total_seconds())
            except Exception:
                pass
    if total_sec >= 3600:
        elapsed_str = "{} 小时 {} 分".format(total_sec // 3600, (total_sec % 3600) // 60)
    elif total_sec >= 60:
        elapsed_str = "{} 分 {} 秒".format(total_sec // 60, total_sec % 60)
    else:
        elapsed_str = "{} 秒".format(total_sec) if total_sec else "—"
    return word_count, health, elapsed_str


def _render_monitoring_card(book, running_tasks: list) -> None:
    """任务实时监控：纸张质感容器 + 进度条 + 左 Glossary 右 实时正文。"""
    if not running_tasks:
        st.info("当前无运行中任务。在「大纲」或「章节」页提交任务后，Worker 将在此显示进度。")
        return
    t = running_tasks[0]
    chapter_title = "—"
    if t.chapter_id:
        ch = next((c for c in book.chapters if c.id == t.chapter_id), None)
        if ch:
            chapter_title = ch.title
    elapsed = "—"
    if t.started_at:
        try:
            delta = datetime.utcnow() - t.started_at
            total_sec = int(delta.total_seconds())
            elapsed = "{} 分 {} 秒".format(total_sec // 60, total_sec % 60)
        except Exception:
            elapsed = "—"
    model_name = book.default_model or "—"
    output = (t.current_output or "").strip()
    out_len = len(output)
    progress_msg = (t.progress_message or "执行中") + ("（约 {} 字）".format(out_len) if out_len else "")
    typical_chars = 3000
    progress_pct = min(1.0, out_len / typical_chars) if typical_chars else 0.0

    st.subheader("📋 任务实时监控")
    r1_c1, r1_c2, r1_c3 = st.columns(3)
    with r1_c1:
        st.metric("当前书名", book.title)
    with r1_c2:
        st.metric("选用模型", model_name)
    with r1_c3:
        st.metric("已运行时间", elapsed)
    st.caption("正在生成：{} · {}".format(chapter_title, progress_msg))
    st.progress(progress_pct, text="生成进度 {:.0%}".format(progress_pct))

    r2_left, r2_right = st.columns([1, 2])
    with r2_left:
        st.markdown("**Glossary（术语表）**")
        terms = list(book.terms)
        if not terms:
            st.caption("暂无术语，章节生成后将自动提取。")
        else:
            for term in terms[-30:]:
                defn = (term.definition or "")[:80]
                if len(term.definition or "") > 80:
                    defn += "…"
                st.text("• {} — {}".format(term.term, defn))
    with r2_right:
        st.markdown("**实时正文预览**")
        with st.container():
            st.markdown(
                '<div style="background:linear-gradient(180deg,#fafaf8 0%,#f5f3ef 100%);'
                'border:1px solid #e0ddd8;border-radius:8px;padding:1.2rem 1.5rem;'
                'box-shadow:inset 0 1px 0 rgba(255,255,255,0.8),0 2px 8px rgba(0,0,0,0.06);">'
                + (
                    (output[:15000] + ("…" if len(output) > 15000 else "")).replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
                    if output else "<span style='color:#888'>等待生成内容…</span>"
                ) + "</div>",
                unsafe_allow_html=True,
            )


# 每 3 秒轮询 SQLite 的监控卡片（若支持 st.fragment 则自动刷新，否则每次整页刷新时更新）
if getattr(st, "fragment", None):
    _monitoring_fragment = st.fragment(run_every=timedelta(seconds=3))(_monitoring_fragment_impl)
else:
    _monitoring_fragment = _monitoring_fragment_impl


def _test_api_connection(model: str) -> str | None:
    """测试 LiteLLM 与指定模型的连接，成功返回 None，失败返回错误信息字符串。"""
    try:
        import litellm
        litellm.completion(
            model=model,
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=64,
        )
        return None
    except Exception as e:
        return str(e)


def _set_task_cancelled(task_id: int) -> None:
    """将指定任务状态改为 CANCELLED，Worker 在 run_task 内 refresh 后会直接返回并提交该状态。"""
    sess = get_session()
    try:
        task = sess.get(GenerationTask, task_id)
        if task:
            task.status = TaskStatus.CANCELLED
            task.progress_message = "已取消（导演强制中断）"
            sess.commit()
    finally:
        sess.close()


def _set_task_cancelled_and_rewrite(task_id: int, book_id: int, chapter_id: int) -> None:
    """将当前任务改为 CANCELLED，并新建一条 REWRITE 任务，Worker 会先处理取消再在下一轮轮询到重写任务。"""
    sess = get_session()
    try:
        task = sess.get(GenerationTask, task_id)
        if task:
            task.status = TaskStatus.CANCELLED
            task.progress_message = "已取消（导演要求重写当前章）"
            sess.flush()
        book = sess.get(Book, book_id)
        ct = (getattr(book, "content_type", None) or "").strip().lower() or "academic"
        if ct not in ("academic", "novel"):
            ct = "academic"
        new_task = GenerationTask(
            book_id=book_id,
            chapter_id=chapter_id,
            task_type=TaskType.REWRITE,
            status=TaskStatus.PENDING,
            params={"content_type": ct},
        )
        sess.add(new_task)
        sess.commit()
    finally:
        sess.close()


# ---------- 控制台首页：进度与冲突 ----------
def page_console(book: Book):
    st.header("创作控制台")
    st.session_state["_monitor_book_id"] = book.id
    session = get_session()
    try:
        b = session.get(Book, book.id)
        if not b:
            st.warning("未找到当前项目")
            return

        # 当前项目下正在运行的任务（异常 FAILED 在 _monitoring_fragment 中展示）
        running_tasks = list(
            session.execute(
                select(GenerationTask).where(
                    GenerationTask.book_id == b.id,
                    GenerationTask.status == TaskStatus.RUNNING,
                ).order_by(GenerationTask.started_at.desc())
            ).scalars().all()
        )

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("项目状态", b.status.value if hasattr(b.status, "value") else str(b.status))
        with col2:
            chapters = [c for c in b.chapters]
            done = sum(1 for c in chapters if c.content)
            st.metric("章节进度", f"{done} / {len(chapters)}" if chapters else "0 / 0")
        with col3:
            st.metric("运行中任务", len(running_tasks))

        # 任务实时监控卡片：每 3 秒从 SQLite 查询 tasks 表并刷新（fragment 独立轮询，不阻塞整页）
        _monitoring_fragment()

        # 强制中断 / 重写当前章 按钮
        if running_tasks:
            st.caption("操作")
            for t in running_tasks:
                btn_col1, btn_col2, _ = st.columns([1, 1, 2])
                with btn_col1:
                    if st.button("强制中断", key="cancel_task_{}".format(t.id), type="secondary"):
                        _set_task_cancelled(t.id)
                        st.rerun()
                with btn_col2:
                    if t.chapter_id and st.button("重写当前章", key="rewrite_task_{}".format(t.id), type="secondary"):
                        _set_task_cancelled_and_rewrite(t.id, t.book_id, t.chapter_id)
                        st.rerun()

        auto_refresh = st.checkbox(
            "自动刷新（每 {} 秒）".format(CONSOLE_REFRESH_INTERVAL),
            value=st.session_state.get("auto_refresh_console", True),
            key="auto_refresh_console",
        )
        if auto_refresh and running_tasks:
            time.sleep(CONSOLE_REFRESH_INTERVAL)
            st.rerun()

        st.subheader("后台任务状态")
        tasks = session.execute(
            select(GenerationTask)
            .where(GenerationTask.book_id == b.id)
            .order_by(GenerationTask.created_at.desc())
            .limit(20)
        ).scalars().all()
        if not tasks:
            st.info("暂无生成任务。请先完成大纲，再在「章节」页点击「开始挂机」。")
        else:
            for t in tasks:
                status_emoji = {"pending": "⏳", "running": "🔄", "completed": "✅", "failed": "❌", "suspended": "⏸️", "cancelled": "🛑"}
                em = status_emoji.get(t.status.value if hasattr(t.status, "value") else t.status, "•")
                st.text(f"{em} [{t.task_type}] {t.status} — {t.progress_message or '-'}")

        st.subheader("冲突预警")
        conflicts = session.execute(
            select(Conflict).where(
                Conflict.book_id == b.id,
                Conflict.resolution == ConflictStatus.PENDING,
            )
        ).scalars().all()
        if not conflicts:
            st.success("当前无待裁决冲突")
        else:
            for c in conflicts:
                with st.expander(f"⚠️ {c.title or c.conflict_type}"):
                    st.write(c.description)
                    if c.context_json:
                        st.json(c.context_json)
                    if st.button("标记为已处理", key=f"resolve_{c.id}"):
                        # 简化：仅标记为接受，实际可弹窗选择裁决类型
                        sess2 = get_session()
                        try:
                            co = sess2.get(Conflict, c.id)
                            if co:
                                co.resolution = ConflictStatus.RESOLVED_ACCEPT
                                sess2.commit()
                                st.rerun()
                        finally:
                            sess2.close()
    finally:
        session.close()


# ---------- 大纲页 ----------
def page_outline(book: Book):
    st.header("全书大纲")
    session = get_session()
    try:
        b = session.get(Book, book.id)
        if not b:
            return
        outline = b.outline
        if not outline:
            st.info("尚未生成大纲。请先填写书名与核心构思，再点击下方按钮。Worker 将使用当前选中的模型生成。")
            _ct = (getattr(b, "content_type", None) or "").strip().lower() or "academic"
            if _ct not in ("academic", "novel"):
                _ct = "academic"
            if st.button("生成全书大纲（Level 1）"):
                sess = get_session()
                try:
                    sess.add(GenerationTask(book_id=b.id, task_type=TaskType.OUTLINE_LEVEL1, status=TaskStatus.PENDING, params={"content_type": _ct}))
                    sess.commit()
                    st.success("已提交任务，请启动 Worker 并到控制台首页查看进度。")
                    st.rerun()
                finally:
                    sess.close()
            return
        st.markdown(outline.content)
        _ct = (getattr(b, "content_type", None) or "").strip().lower() or "academic"
        if _ct not in ("academic", "novel"):
            _ct = "academic"
        if st.button("重新生成大纲"):
            sess = get_session()
            try:
                sess.add(GenerationTask(book_id=b.id, task_type=TaskType.OUTLINE_LEVEL1, status=TaskStatus.PENDING, params={"content_type": _ct}))
                sess.commit()
                st.success("已提交新任务。")
                st.rerun()
            finally:
                sess.close()
        if outline.raw_json and not list(b.chapters):
            st.divider()
            if st.button("从大纲创建章节骨架"):
                import json
                try:
                    arr = json.loads(outline.raw_json)
                except Exception:
                    arr = []
                if arr:
                    sess = get_session()
                    try:
                        for i, item in enumerate(arr):
                            idx = item.get("chapter_index", i + 1)
                            title = item.get("title") or "第{}章".format(idx)
                            sess.add(Chapter(book_id=b.id, order_index=idx, title=title))
                        sess.commit()
                        st.success("已创建 {} 个章节，可到「章节」页扩写。".format(len(arr)))
                        st.rerun()
                    finally:
                        sess.close()
                else:
                    st.warning("无法解析大纲 JSON。")
    finally:
        session.close()


# ---------- 章节页 ----------
def page_chapters(book: Book):
    st.header("章节列表")
    session = get_session()
    try:
        b = session.get(Book, book.id)
        if not b:
            return
        chapters = sorted(b.chapters, key=lambda c: c.order_index)
        if not chapters:
            st.info("暂无章节。请先生成全书大纲，系统将自动创建章节骨架。")
            return
        for ch in chapters:
            with st.expander(f"第 {ch.order_index} 章 · {ch.title} | 状态: {ch.status}"):
                if ch.outline_content:
                    st.caption("论证细纲")
                    st.text(ch.outline_content[:500] + "..." if len(ch.outline_content or "") > 500 else (ch.outline_content or ""))
                if ch.content:
                    st.caption("正文预览")
                    st.markdown((ch.content or "")[:2000] + ("..." if len(ch.content or "") > 2000 else ""))
                _ct = (getattr(b, "content_type", None) or "").strip().lower() or "academic"
                if _ct not in ("academic", "novel"):
                    _ct = "academic"
                _params = {"content_type": _ct}
                col1, col2 = st.columns(2)
                with col1:
                    if st.button("扩写/重写本章", key=f"rewrite_{ch.id}"):
                        sess = get_session()
                        try:
                            sess.add(GenerationTask(book_id=b.id, chapter_id=ch.id, task_type=TaskType.REWRITE if ch.content else TaskType.CHAPTER, status=TaskStatus.PENDING, params=_params))
                            sess.commit()
                            st.success("已提交任务，Worker 将按当前模型生成。")
                            st.rerun()
                        finally:
                            sess.close()
                with col2:
                    if st.button("审计本章", key=f"audit_{ch.id}"):
                        sess = get_session()
                        try:
                            sess.add(GenerationTask(book_id=b.id, chapter_id=ch.id, task_type=TaskType.AUDIT, status=TaskStatus.PENDING, params=_params))
                            sess.commit()
                            st.success("已提交审计任务。")
                            st.rerun()
                        finally:
                            sess.close()
        st.divider()
        _ct = (getattr(b, "content_type", None) or "").strip().lower() or "academic"
        if _ct not in ("academic", "novel"):
            _ct = "academic"
        if st.button("开始挂机（按序生成未完成章节）"):
            sess = get_session()
            try:
                n = 0
                for ch in chapters:
                    if not ch.content:
                        sess.add(GenerationTask(book_id=b.id, chapter_id=ch.id, task_type=TaskType.CHAPTER, status=TaskStatus.PENDING, params={"content_type": _ct}))
                        n += 1
                sess.commit()
                st.success("已提交 {} 个章节任务。".format(n) if n else "当前无未写章节。")
                st.rerun()
            finally:
                sess.close()
    finally:
        session.close()


# ---------- 知识中枢 ----------
def page_knowledge(book: Book):
    st.header("知识中枢")
    tab1, tab2, tab3 = st.tabs(["术语表", "论证链", "参考文献"])
    session = get_session()
    try:
        b = session.get(Book, book.id)
        if not b:
            return
        with tab1:
            terms = list(b.terms)
            if not terms:
                st.info("术语表为空。章节生成后将自动提取专有名词。")
            else:
                for t in terms:
                    st.text(f"**{t.term}** — {t.definition or '(无定义)'} (首现: 章 {t.first_chapter_id})")
        with tab2:
            args = list(b.arguments)
            if not args:
                st.info("论证链为空。写作与审计过程中将自动记录核心论点。")
            else:
                for a in args:
                    txt = a.argument_text or ""
                    st.text(f"• {txt[:200]}{'...' if len(txt) > 200 else ''}")
        with tab3:
            refs = list(b.references)
            if not refs:
                st.info("尚未导入参考文献。支持上传 PDF/Markdown 或录入 BibTeX。")
                if st.button("导入文献"):
                    st.info("文献导入功能接入后此处可上传或粘贴")
            else:
                for r in refs:
                    st.text(f"[{r.citation_key}] {r.title or r.file_path}")
    finally:
        session.close()


# ---------- 系统设定页（默认模型、语气、API 测试） ----------
def page_settings(book: Book):
    st.header("系统设定")
    session = get_session()
    try:
        b = session.get(Book, book.id)
        if not b:
            return
        model_index = next((i for i, m in enumerate(DEFAULT_MODELS) if m == (b.default_model or "")), 0)
        default_model = st.selectbox("默认模型", DEFAULT_MODELS, key="settings_model", index=model_index)
        tone_options = ["严谨", "通俗", "批判性"]
        tone_index = tone_options.index(b.tone) if (b.tone and b.tone in tone_options) else 0
        tone = st.radio("学术语气", tone_options, key="settings_tone", horizontal=True, index=tone_index)
        if st.button("保存全局参数", key="settings_save"):
            sess = get_session()
            try:
                bk = sess.get(Book, book.id)
                if bk:
                    bk.default_model = default_model
                    bk.tone = tone
                    sess.commit()
                    st.success("已保存")
                    st.rerun()
            finally:
                sess.close()
        st.divider()
        st.subheader("API 连接测试")
        api_provider = st.selectbox("选择提供商", list(API_TEST_PROVIDERS.keys()), key="settings_api_provider")
        if st.button("测试连接", key="settings_api_btn"):
            model = API_TEST_PROVIDERS.get(api_provider)
            if model:
                with st.spinner("正在连接 {}…".format(api_provider)):
                    err = _test_api_connection(model)
                    if err:
                        st.error("连接失败：{}".format(err))
                    else:
                        st.success("{} 连接成功".format(api_provider))
    finally:
        session.close()


# ---------- 主入口与页面路由 ----------
def main():
    current_book = render_sidebar()
    if not current_book:
        st.info("请在左侧选择已有项目，或创建新书籍项目。")
        return

    theme = "novel" if (getattr(current_book, "content_type", None) or "").strip().lower() == "novel" else "academic"
    wide_mode = st.session_state.get("wide_mode", True)
    _inject_custom_css(theme=theme, wide_mode=wide_mode)

    session = get_session()
    try:
        b = session.get(Book, current_book.id)
        if not b:
            st.warning("未找到当前项目")
            return
        word_count, logic_health, total_elapsed = _book_kpis(session, b)
    finally:
        session.close()

    k1, k2, k3, k4 = st.columns(4)
    with k1:
        st.metric("当前书名", (current_book.title or "—")[:20] + ("…" if len(current_book.title or "") > 20 else ""))
    with k2:
        st.metric("已写字数", "{:,}".format(word_count))
    with k3:
        st.metric("逻辑健康度", "{}%".format(logic_health))
    with k4:
        st.metric("累计耗时", total_elapsed)

    tab_console, tab_content, tab_knowledge, tab_settings = st.tabs(["🎬 创作控制台", "📝 内容看板", "🧠 学术/人设库", "⚙️ 系统设定"])

    with tab_console:
        page_console(current_book)

    with tab_content:
        sub_tab_a, sub_tab_b = st.tabs(["全书大纲", "章节列表"])
        with sub_tab_a:
            page_outline(current_book)
        with sub_tab_b:
            page_chapters(current_book)

    with tab_knowledge:
        page_knowledge(current_book)

    with tab_settings:
        page_settings(current_book)


if __name__ == "__main__":
    main()