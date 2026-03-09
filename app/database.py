# -*- coding: utf-8 -*-
"""兼容层：从 database 包导出，保持 app.database 引用有效"""
from database import get_session, init_db

__all__ = ["init_db", "get_session"]
