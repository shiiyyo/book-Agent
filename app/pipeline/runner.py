# -*- coding: utf-8 -*-
"""
流水线任务调度与执行
创建/轮询 GenerationTask，调用 outline / chapter / audit，支持后台 asyncio 或 Celery。
"""
