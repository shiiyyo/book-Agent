# Academic-Director 需求规格与项目目录

> 学术书籍自动化创作系统 — 导演级创作环境，本地优先、异步流水线、术语强一致。

---

## 1. 项目愿景

为专业学者和严肃作家提供「导演级」自动化创作环境：AI 在后台 24 小时遵循严谨逻辑、参考专业文献、自动维护学术术语一致性，用户负责定义假设、大纲、文献与高层指令。

---

## 2. 核心架构与模式

| 维度 | 说明 |
|------|------|
| **创作模式** | 导演模式：用户定义研究假设、大纲、参考文献与高层指令；AI 执行长文本扩写。 |
| **运行模式** | 异步生产流水线：AI 后台静默运行，不干扰前端策划，用户随时登录审阅。 |
| **存储策略** | 本地优先：SQLite/PostgreSQL 存储书稿与逻辑资产，支持后续迁移云端（如 Supabase）。 |

---

## 3. 功能模块与对应实现

### 3.1 知识中枢 (Semantic Core)

| 机制 | 规格要求 | 当前实现 |
|------|----------|----------|
| **自动术语表** | 每章写完后自动提取专有名词；后续章节生成时强制将术语表作为上下文底座 | `app/models.py` → `Term` 表；Pipeline 接入后实现提取与注入 |
| **论证链追踪** | 记录全书核心论点与推导逻辑，确保后章不违背前章假设 | `Argument` 表；审计阶段写入，生成前校验 |
| **参考文献关联** | 支持导入 PDF/Markdown，AI 写作时标注来源 ID，引文可追溯 | `Reference` + `Citation` 表；文献导入与引用解析待实现 |

### 3.2 自动化流水线 (Pipeline)

| 层级 | 输入 → 输出 | 当前实现 |
|------|-------------|----------|
| **Level 1** | 书名 + 核心构思 → 全书大纲 | `GenerationTask.task_type = outline_l1`，逻辑待实现 |
| **Level 2** | 章节标题 → 论证细纲 | `Outline` + `Chapter.outline_content`，任务类型 `outline_l2` |
| **Level 3** | 细纲 + 参考文献 → 章节正文 | `Chapter.content`，任务类型 `chapter` |
| **逻辑校验环** | 每章生成后 AI 审计：逻辑冲突、术语误用、口语化 | 任务类型 `audit`，冲突写入 `Conflict` 表 |

### 3.3 导演控制台 (Director Console)

| 功能 | 规格要求 | 当前实现 |
|------|----------|----------|
| 进度监视器 | 实时显示后台 AI 思考进度与生成状态 | `main.py` → 控制台首页展示 `GenerationTask` 列表与状态 |
| 全局参数 | 一键切换模型、学术语气（严谨/通俗/批判性） | 侧边栏：模型选择、语气 radio，写入 `Book.default_model` / `Book.tone` |
| 冲突决策 | AI 发现逻辑冲突时弹出预警，等待导演裁决 | `Conflict` 表 + 控制台「冲突预警」区，可标记已处理 |

---

## 4. 技术栈与项目目录

### 4.1 技术栈

- **开发环境**：Cursor (AI 驱动开发)
- **AI 调用**：LiteLLM（统一接口，多模型动态切换）
- **前端**：Streamlit（交互式控制台、异步状态显示）
- **后端/任务调度**：Python asyncio 或 Celery（后台挂机写作）
- **数据库**：SQLite / PostgreSQL（SQLAlchemy，兼容 Supabase）
- **输出格式**：学术 Markdown（LaTeX 公式、BibTeX 引用）

### 4.2 项目目录结构

```
Academic-Director/
├── database/                  # 数据库层
│   ├── __init__.py
│   ├── models.py              # Book, Outline, Chapter, Term, Argument, Reference, Citation, GenerationTask, Conflict
│   └── connection.py          # 引擎、会话、建表（SQLite/PostgreSQL）
├── app/
│   ├── __init__.py
│   ├── config.py              # LiteLLM/API 配置
│   ├── models.py              # 兼容：从 database.models 再导出
│   ├── database.py            # 兼容：从 database 导出 init_db / get_session
│   ├── pipeline/              # 自动化流水线（待接入 LiteLLM）
│   │   ├── __init__.py
│   │   ├── outline.py         # Level 1 全书大纲、Level 2 论证细纲
│   │   ├── chapter.py         # Level 3 章节正文
│   │   ├── audit.py           # 逻辑校验环
│   │   └── runner.py          # 任务调度与执行
│   └── semantic/              # 知识中枢
│       ├── __init__.py
│       ├── glossary.py        # 自动术语表
│       ├── argument_tracker.py # 论证链追踪
│       └── citation_manager.py # 参考文献与引用
├── data/                      # 本地数据目录（SQLite 数据库存放于此）
│   └── .gitkeep
├── main.py                    # Streamlit 导演控制台入口
├── requirements.txt
├── README.md
├── SPEC.md
└── .gitignore
```

---

## 5. 使用场景示例（规格）

1. **立项**：Streamlit 输入书名《量子纠缠在分布式计算中的应用》并上传 5 篇核心论文。
2. **策划**：AI 提出 10 章大纲，导演调整第 3、4 章顺序，点击「开始挂机」。
3. **生产**：AI 后台运行；第 1 章写完后自动提取术语「Bell State」入库。
4. **审阅**：用户次日查看前三章，在控制台下令：「第 2 章推导过简，请加入参考文献 B 第 4 节数据重新扩写。」

---

## 6. 异常处理逻辑

| 场景 | 处理方式 |
|------|----------|
| 逻辑冲突 | AI 发现与术语库/论证链冲突 → 挂起任务，写入 `Conflict`，控制台预警，等待导演裁决 |
| API 中断 | 任务 `checkpoint` 记录断点，恢复后从当前章节继续 |
| Token 溢出 | 滚动窗口 + 核心摘要，保证前文核心定义不丢失 |

---

## 7. Word 导出排版规范（交稿统一）

以下规范前后端一致，Word 导出即可直接用于交稿排版：

- **正文**：宋体小四、1.5 倍行距、首行缩进 2 字符、两端对齐。
- **一级标题**（如「第一章」）：黑体三号、居中。
- **二级标题**（如「一、」）：黑体四号、左对齐。
- **三级标题**（如「（一）」）：楷体小四、左对齐。
- **强调**：仅加粗，不使用斜体等。
- 导出即可直接用于交稿排版。

实现位置：`api_server.py` 中 `_book_to_docx`；前端在「选择书籍」卡片导出按钮旁展示本规范说明。

---

## 8. 验收标准对照

| 验收项 | 状态 | 说明 |
|--------|------|------|
| 能够基于本地数据库独立运行 | ✅ 已具备 | SQLite 默认，`init_db()` 建表，Streamlit 可独立启动 |
| 术语强一致：第 5 章与第 1 章关键概念称呼完全对齐 | 🔲 待实现 | 依赖 Pipeline：生成时注入术语表、审计时校验 |
| 后台异步写作，刷新前端不中断生成任务 | 🔲 待实现 | 依赖 asyncio/Celery 后台进程，任务状态持久化在 DB |
| Word 导出符合交稿排版规范（§7） | ✅ 已具备 | 正文宋体小四 1.5 倍行距首行缩进 2 字符两端对齐；一/二/三级标题黑体/黑体/楷体；强调仅加粗 |

---

*文档版本与项目代码同步更新。*
