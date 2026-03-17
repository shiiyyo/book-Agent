# -*- coding: utf-8 -*-
"""
Pipeline（编排层）

目的：
- 将“任务编排/质量控制/后处理”从 core.engine（生成实现）中逐步拆出
- 保持对外接口兼容（api_server 仍可像以前一样调用 run_task）
"""

# -*- coding: utf-8 -*-
"""
自动化流水线：Level 1/2/3 生成、审计、断点续传
- outline: Level 1 全书大纲、Level 2 论证细纲
- chapter: Level 3 章节正文
- audit: 逻辑校验环
- runner: 任务调度与执行（待接入 LiteLLM）
"""
# from app.pipeline.outline import ...
# from app.pipeline.chapter import ...
# from app.pipeline.audit import ...
# from app.pipeline.runner import ...
