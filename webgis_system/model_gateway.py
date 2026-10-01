"""云端兼容 Chat Completions 与本地 Ollama 的统一消息入口。"""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import HTTPException

import config


def chat(messages, *, tools=None, tool_choice="auto", json_mode=False, timeout=None):
    """返回统一的 assistant message；工具参数保留提供商原值供调用端校验。"""
    provider = config.MODEL_PROVIDER
    if provider == "none":
        raise HTTPException(503, "当前未启用模型")
    local = provider == "ollama"
    if local:
        base = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
        url = base + "/api/chat"
        payload = {"model": os.environ.get("OLLAMA_MODEL", "qwen2.5:7b"),
                   "messages": messages, "stream": False,
                   "options": {"temperature": 0}}
        if json_mode:
            payload["format"] = "json"
        headers = {"Content-Type": "application/json"}
    else:
        base = os.environ.get("MODEL_API_BASE_URL", "").rstrip("/")
        key = os.environ.get("MODEL_API_KEY", "")
        if not base or not key:
            raise HTTPException(503, "云端模型未配置 MODEL_API_BASE_URL / MODEL_API_KEY")
        url = base + "/chat/completions"
        payload = {"model": os.environ.get("MODEL_NAME", "gpt-5.6-sol"),
                   "messages": messages, "temperature": 0}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
    if tools is not None:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice
    request = Request(url, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                      headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout or (120 if local else 60)) as response:
            data = json.load(response)
        message = data["message"] if local else data["choices"][0]["message"]
        if not isinstance(message, dict):
            raise ValueError("模型消息格式错误")
        return message
    except HTTPError as exc:
        # 不输出响应正文，避免上游在错误页面中回显密钥或请求内容。
        raise HTTPException(503, f"模型服务返回 HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError, ValueError, KeyError, IndexError,
            TypeError) as exc:
        raise HTTPException(503, f"模型服务未就绪：{type(exc).__name__}") from exc


def mode_name():
    return config.MODEL_PROVIDER
