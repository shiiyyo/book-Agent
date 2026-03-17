## app/pipeline（编排层）拆分建议

当前代码现状：
- `core/engine.py` 同时承担：prompt 设计、任务编排、字数补齐、分节生成、后处理（引用同步）等职责。
- `api_server.py` 同时承担：HTTP 路由、后台线程 worker、SSE、导出 docx、（未来）references API 等。
- `static/index.html` 是单文件 UI，随着功能增加会快速变得难维护。

### 1) 是否需要把 `core/engine.py` 拆到 `app/pipeline/`？

**建议：需要，但分两步。**

- **第一步（已完成）**：新增 `app/pipeline/task_runner.py`，作为“对外执行入口”。`api_server.py` 只调用 `run_generation_task()`。
- **第二步（逐步迁移）**：把以下逻辑从 `core/engine.py` 迁到 `app/pipeline/`，并保持 `core/engine.py` 仅保留“模型调用/生成算法”：
  - 质量门控：重复检测、事实一致性校验、章节结构约束
  - 后处理：解析 `[Source_ID]`，同步到 `citations`
  - 阶段策略：chapter_draft / section_finalize 的 prompt 版本管理

这样做的收益：
- 引擎可替换（不同模型/不同 prompt 版本）不影响 API。
- 更容易做 A/B 测试、质量指标、缓存与重试策略。

### 2) 是否需要拆分 `api_server.py`？

**建议：需要。**（但不必一次性大拆）

推荐按 Flask Blueprint 拆到：
- `app/api/books.py`：书籍 CRUD、outline/chapters 的保存接口
- `app/api/tasks.py`：enqueue、cancel、SSE stream
- `app/api/export.py`：md/docx 导出
- `app/api/references.py`：参考文献上传/URL 导入/删除

入口 `api_server.py` 只保留：create_app + 蓝图注册 + main 启动。

### 3) 是否需要拆分 `static/index.html`？

**建议：尽快拆。**（尤其在加入 references UI 后）

最小拆分方案（仍然纯原生 JS，不引入框架）：
- `static/index.html`：骨架 + root 容器
- `static/app.js`：状态管理与 API 调用
- `static/ui.js`：DOM 渲染组件（书籍列表、章节编辑器、任务进度条、references 面板）
- `static/styles.css`：样式

好处：
- 改动更可控，避免单文件冲突与回归。
- 更易做“质量提示 UI”（重复告警、事实错误告警、引用缺失提醒）。

