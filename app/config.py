# -*- coding: utf-8 -*-
"""全局配置：LiteLLM / API 密钥 / 模型列表；启动时加载 .env"""
import os

from dotenv import load_dotenv

# 优先从项目根目录加载 .env（DeepSeek、Moonshot、OpenAI、Anthropic 等）
load_dotenv()

# API 密钥与 Base（.env 中配置后自动生效）
LITELLM_API_BASE = os.getenv("LITELLM_API_BASE", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_BASE = os.getenv("DEEPSEEK_API_BASE", "")
MOONSHOT_API_KEY = os.getenv("MOONSHOT_API_KEY", "")
MOONSHOT_API_BASE = os.getenv("MOONSHOT_API_BASE", "")
VOLCENGINE_API_KEY = os.getenv("VOLCENGINE_API_KEY", "")

# API 连接测试：显示名 -> LiteLLM 模型名
API_TEST_PROVIDERS = {
    "DeepSeek": "deepseek/deepseek-chat",
    "Kimi": "moonshot/moonshot-v1-32k",
    "豆包": "volcengine/doubao-pro-32k",
}

# 控制台可选模型：选中的模型会用于本书生成（LiteLLM 根据前缀选用对应 API）
DEFAULT_MODELS = [
    "deepseek/deepseek-chat",
    "moonshot/moonshot-v1-128k",
    "moonshot/moonshot-v1-32k",
    "volcengine/doubao-pro-32k",
    "gpt-4o-mini",
    "gpt-4o",
    "claude-3-5-sonnet",
    "claude-3-opus",
]
