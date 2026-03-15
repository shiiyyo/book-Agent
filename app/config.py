# -*- coding: utf-8 -*-
"""全局配置：LiteLLM / API 密钥 / 模型列表；启动时加载 .env"""
import os
from pathlib import Path

from dotenv import load_dotenv

# 始终从项目根目录加载 .env，避免因工作目录不同而读到错误或旧配置
_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env")

# 彻底禁用 DeepSeek / Moonshot，避免 LiteLLM 误用旧环境变量导致仍走 DeepSeek 报错
for _k in ("DEEPSEEK_API_KEY", "DEEPSEEK_API_BASE", "MOONSHOT_API_KEY", "MOONSHOT_API_BASE"):
    os.environ.pop(_k, None)

# API 密钥与 Base（.env 中配置后自动生效）
LITELLM_API_BASE = os.getenv("LITELLM_API_BASE", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_API_BASE = os.getenv("OPENAI_API_BASE", "") or os.getenv("OPENAI_BASE_URL", "")
if not os.getenv("OPENAI_API_BASE") and os.getenv("OPENAI_BASE_URL"):
    os.environ["OPENAI_API_BASE"] = os.environ.get("OPENAI_BASE_URL", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
VOLCENGINE_API_KEY = os.getenv("VOLCENGINE_API_KEY", "")

# 调用模型：仅从 .env 读取，不写死默认。填什么就调什么。
# 支持 OPENAI_MODEL 或 DEFAULT_MODEL，例如：openai/grok-4-0709、gpt-4o-mini
_MODEL_FROM_ENV = (os.getenv("OPENAI_MODEL") or os.getenv("DEFAULT_MODEL") or "").strip()


def get_default_model() -> str:
    """返回当前要调用的模型（来自 .env）。未配置时返回空字符串，调用方需处理。"""
    return _MODEL_FROM_ENV


# API 连接测试：显示名 -> LiteLLM 模型名
API_TEST_PROVIDERS = {
    "豆包": "volcengine/doubao-pro-32k",
    "OpenAI": "gpt-4o-mini",
    "Claude": "claude-3-5-sonnet",
}

# 可选模型列表（仅用于 Streamlit 等需要下拉时；实际调用以 .env 为准）
DEFAULT_MODELS = [
    "gpt-4o-mini",
    "gpt-4o",
    "volcengine/doubao-pro-32k",
    "claude-3-5-sonnet",
    "claude-3-opus",
]
