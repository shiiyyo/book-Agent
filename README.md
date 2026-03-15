# Academic-Director（学术书籍自动化创作系统）

为专业学者和严肃作家提供的「导演级」自动化创作环境：用户定义假设与大纲，AI 在后台按严谨逻辑与术语一致性进行长文本扩写。

---

## 一、项目框架

### 1.1 定位与愿景

| 维度 | 说明 |
|------|------|
| **创作模式** | **导演模式**：用户定义研究假设、全书大纲、参考文献与高层指令；AI 执行 Level 1→2→3 的阶梯式生成（大纲 → 细纲 → 正文）。 |
| **运行模式** | 支持两种形态：**单进程网页版**（Flask + 同步执行，无需单独 Worker）与 **双进程**（Streamlit 控制台 + 后台 Worker 轮询，适合挂机写作）。 |
| **存储策略** | **本地优先**：SQLite / PostgreSQL 存储书稿、大纲、术语表、任务与冲突；支持导出 Markdown / Word。 |

### 1.2 核心架构示意

```
┌─────────────────────────────────────────────────────────────────┐
│                    导演控制台（Web / Streamlit）                    │
│  选书·模型·生成大纲·从大纲建章·扩写本章·进度监视·导出 Word/MD         │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│                     API / 任务调度层                              │
│  api_server.py（Flask 同步执行） 或  main.py + tasks/worker.py    │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│                     生成引擎 core/engine.py                        │
│  LiteLLM 多模型 · 学术 System Prompt · 重试 · 大纲/细纲/正文/审计   │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│  知识中枢（app/semantic）  │  数据库层（database/）               │
│  术语表·论证链·参考文献     │  Book / Outline / Chapter / Term /   │
│  写章前注入·写章后提取      │  GenerationTask / Conflict          │
└─────────────────────────────────────────────────────────────────┘
```

---

## 二、需求与目标

### 2.1 用户与场景

- **谁在用**：需要撰写学术专著、教材或长篇严肃内容的学者、作者、研究团队。
- **痛点**：长文本写作耗时、术语前后不一致、论证结构难统一、希望「定好框架后由 AI 按规范扩写」。
- **典型流程**：立项（书名 + 核心构思）→ 生成全书大纲 → 从大纲创建章节骨架 → 对每章点击「扩写本章」→ 审阅 / 导出。

### 2.2 产品目标

- 用户只负责**定义假设与大纲**，AI 负责在**术语一致、论证闭环、学术语气**约束下生成正文。
- 支持**多模型切换**（DeepSeek、Kimi、OpenAI、Claude 等，经 LiteLLM 统一接口）。
- 支持**导出**：Markdown 与 Word（含大纲 + 章节正文）。

### 2.3 技术目标

- **术语强一致**：写章前将本书术语表注入 Prompt，写章后从正文中提取新术语入库，供后续章节使用。
- **可审计**：每章可触发「审计」任务，由 LLM 检查术语一致性、论证闭环与口语化。
- **可扩展**：流水线（outline_l1 / outline_l2 / chapter / audit）与知识中枢（术语、论证链、参考文献）模块化，便于接入文献导入、冲突裁决等。

### 2.4 功能边界（当前）

- **已实现**：全书大纲生成（Level 1）、从大纲 JSON 创建章节、单章扩写（Level 3）、术语表写章前注入与写章后自动提取、审计占位、网页版导演控制台（大纲/正文 Markdown 渲染、大纲中 JSON 不展示）、导出 MD/DOCX、任务状态与错误展示。
- **部分实现 / 预留**：Level 2 论证细纲（引擎支持，前端可扩展为「按章生成细纲」）；参考文献表结构与引擎注入已存在，文献导入与引用解析待完善；冲突裁决（Conflict 表 + 控制台预警）待接入流水线。

---

## 三、实现路径

### 3.1 流水线层级（Pipeline）

| 层级 | 任务类型 | 输入 | 输出 | 实现位置 |
|------|----------|------|------|----------|
| **Level 1** | `outline_l1` | 书名 + 核心构思 | 全书大纲（Markdown + 末尾 JSON 数组） | `core/engine.py` → `_run_outline_l1`；保存时剥离 JSON 至 `raw_json`，展示仅用 Markdown。 |
| **Level 2** | `outline_l2` | 全书大纲摘要 + 章节标题 | 本章论证细纲（Markdown） | `_run_outline_l2` → 写入 `Chapter.outline_content`。 |
| **Level 3** | `chapter` / `rewrite` | 本章细纲 + 术语表 + 参考文献 | 章节正文（Markdown） | `_run_chapter`；写章前从 `Term` 表加载术语注入 Prompt，写章后可由 Worker 调用 `app.semantic.glossary` 提取新术语入库。 |
| **审计** | `audit` | 本章正文 | 审计要点（术语一致性 / 论证闭环 / 口语化） | `_run_audit`，结果写回 `task.current_output`，章节状态可更新为 AUDITED。 |

流水线任务由 **GenerationTask** 持久化（PENDING → RUNNING → COMPLETED/FAILED），支持进度与断点展示；网页版在 API 内同步执行 `run_task`，双进程模式下由 `tasks/worker.py` 轮询执行。

### 3.2 知识中枢（Semantic）

- **术语表（Term）**：按书维度存储专有名词与定义；写章前格式化为 `[Pre-defined_Glossary]` 注入；写章后通过 `app.semantic.glossary.extract_definitions_and_terms` 从正文提取新术语写入 Term（Worker 中已接入）。
- **论证链（Argument）**：表结构已存在，用于记录全书核心论点与推导逻辑；审计与冲突裁决可在此基础上扩展。
- **参考文献（Reference + Citation）**：Reference 按书存储，章节扩写时 `_format_references` 注入 `[Reference]` 块；文献导入与引用解析见 `app.semantic.citation_manager`，待完善。

### 3.3 导演控制台

- **网页版（推荐）**：`static/index.html` + `api_server.py`。选择/新建书籍 → 选模型 → 生成全书大纲 → 从大纲创建章节 → 对每章「扩写本章」；大纲与正文区域使用 Markdown 渲染（marked.js），大纲展示时过滤掉 JSON 片段；支持导出 Word / Markdown、当前任务与错误提示。
- **Streamlit 版**：`main.py`。侧边栏选书/新建、模型与语气、控制台首页（任务进度与流式输出）、大纲页、章节页（扩写/审计/挂机）、知识中枢入口；需配合 `python -m tasks.worker` 实现后台挂机。

### 3.4 数据层与模型

- **database/**：`models.py` 定义 Book、Outline、Chapter、Term、Argument、Reference、Citation、GenerationTask、Conflict 及枚举（BookStatus、TaskType、TaskStatus 等）；`connection.py` 负责引擎、会话与建表（SQLite/PostgreSQL）。
- **app/**：`app.models` / `app.database` 为兼容层，从 `database` 再导出，供 pipeline、semantic 及旧入口使用。

---

## 四、快速开始

### 4.1 方式一：网页版（单进程，推荐）

不依赖 Streamlit，一个命令启动网页版导演控制台。

```bash
cd Academic-Director
pip install -r requirements.txt
```

在项目根目录配置 `.env`（可复制 `.env.example` 为 `.env` 后修改）：

- **必填**：`OPENAI_MODEL=...` 或 `DEFAULT_MODEL=...`（例如 `OPENAI_MODEL=openai/gpt-4o-mini` 或 `OPENAI_MODEL=claude-3-7-sonnet-20250219`），否则会报「未配置调用模型」。
- 按所选模型配置对应 API Key（如 `OPENAI_API_KEY`、`ANTHROPIC_API_KEY` 等）。

然后：

```bash
python api_server.py
```

浏览器打开 **http://127.0.0.1:5000**：选择/新建书籍 → 选模型 → 生成全书大纲 → 从大纲创建章节 → 对每一章点击「扩写本章」。生成过程在同一页面完成，无需再开 Worker。

### 4.2 方式二：Streamlit + Worker（双进程）

在项目根目录放置 `.env`（可参考 `.env.example`），**必须设置调用模型**：

```env
# 必填其一
OPENAI_MODEL=openai/gpt-4o-mini
# 或 DEFAULT_MODEL=claude-3-5-sonnet

# 按模型配置对应 Key
OPENAI_API_KEY=sk-xxx
# 可选代理
# OPENAI_API_BASE=https://your-gateway.com/v1
```

两个终端分别启动：

```bash
# 终端 1：导演控制台
streamlit run main.py

# 终端 2：后台 Worker（轮询任务并调用引擎）
python -m tasks.worker
```

默认使用项目下 `data/academic_director.db`（SQLite）。使用 PostgreSQL 时设置环境变量：

```bash
set DATABASE_URL=postgresql://user:pass@host/dbname
streamlit run main.py
```

---

## 五、项目结构（与实现路径对应）

```
Academic-Director/
├── database/                  # 数据层
│   ├── __init__.py            # 导出 Base、init_db、get_session、所有模型与枚举
│   ├── models.py              # Book, Outline, Chapter, Term, Argument, Reference, Citation, GenerationTask, Conflict
│   └── connection.py          # 引擎、会话、建表（SQLite/PostgreSQL）
├── app/
│   ├── config.py              # LiteLLM/API 配置
│   ├── models.py / database.py # 兼容：从 database 再导出
│   ├── pipeline/              # 流水线（Level 1/2/3、审计、调度）
│   │   ├── outline.py         # Level 1 全书大纲、Level 2 论证细纲
│   │   ├── chapter.py         # Level 3 章节正文
│   │   ├── audit.py           # 逻辑校验环
│   │   └── runner.py          # 任务调度与执行
│   └── semantic/              # 知识中枢
│       ├── glossary.py        # 自动术语表（提取与注入）
│       ├── argument_tracker.py # 论证链追踪
│       └── citation_manager.py # 参考文献与引用
├── core/
│   └── engine.py              # 生成引擎：LiteLLM + 学术 Prompt，outline_l1/l2、chapter、audit，重试与写回 DB
├── tasks/
│   └── worker.py              # 轮询 PENDING 任务 → 写章前加载术语、run_task、写章后提取术语入库
├── static/
│   └── index.html             # 网页版导演控制台（Markdown 渲染、大纲去 JSON）
├── data/                      # 本地数据目录（SQLite 等）
├── api_server.py              # Flask 极简后端 + 同步执行生成（网页版入口）
├── main.py                    # Streamlit 导演控制台入口
├── requirements.txt
├── README.md
├── SPEC.md                    # 需求规格与目录对照
└── .gitignore
```

---

## 六、后续可扩展方向

- **Pipeline 完善**：Level 2 细纲在前端的「按章生成」入口；审计结果与 Conflict 联动；断点续传与 checkpoint 持久化。
- **文献导入**：PDF/Markdown 解析、引用键管理、与 Reference/Citation 的完整打通。
- **后台异步**：asyncio 或 Celery 实现「挂机」写作，与现有 Worker 轮询并存或替代。
- **冲突裁决**：AI 发现与术语/论证链冲突时挂起任务、写入 Conflict，控制台预警并等待导演裁决后继续。

---

*文档与代码同步更新；详细规格见 SPEC.md。*
