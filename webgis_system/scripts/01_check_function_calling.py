r"""检查当前 Ollama 或兼容 Chat Completions 接口是否返回标准工具调用。

本脚本只询问模型会调用什么工具，不执行数据库查询，也不保存密钥。
PowerShell: .\.venv\Scripts\python.exe .\scripts\01_check_function_calling.py --provider local
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from model_gateway import chat, mode_name  # noqa: E402


TOOL = {
    "type": "function",
    "function": {
        "name": "get_annual_area",
        "description": "查询官渡区指定年份各类土地覆盖面积。",
        "parameters": {
            "type": "object",
            "properties": {
                "year": {"type": "integer", "description": "1990 至 2025 的年份"}
            },
            "required": ["year"],
            "additionalProperties": False,
        },
    },
}


def main():
    parser = argparse.ArgumentParser(description="检测模型是否返回 tool_calls")
    parser.add_argument("--provider", choices=("local", "cloud"),
                        help="仅核对当前 MODEL_PROVIDER，不修改模式")
    args = parser.parse_args()
    provider = mode_name()
    if provider == "none":
        parser.error("当前未启用模型")
    if args.provider and args.provider != {"ollama": "local", "cloud": "cloud"}[provider]:
        parser.error("--provider 与 MODEL_PROVIDER 不一致")
    messages = [
            {"role": "system", "content": "你需要查询真实数据。只能调用给定工具；不要猜测面积数值。"},
            {"role": "user", "content": "请查询官渡区 2025 年各类土地覆盖面积。"},
        ]
    print(f"服务：{provider}；正在测试工具选择……", flush=True)
    try:
        message = chat(messages, tools=[TOOL], timeout=120)
        calls = message.get("tool_calls") or []
        if not calls:
            print("没有收到 tool_calls。模型可能未选择工具，或服务不支持该格式。")
            print("模型文本（截取前 300 字）：", str(message.get("content") or "")[:300])
            return 1
        for call in calls:
            function = call.get("function") or {}
            raw_arguments = function.get("arguments", {})
            parsed = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
            print("工具名称：", function.get("name"))
            print("工具参数：", json.dumps(parsed, ensure_ascii=False))
            if function.get("name") != "get_annual_area" or parsed != {"year": 2025}:
                print("已返回工具调用，但工具名称或年份错误。")
                return 1
        print("通过：模型返回了结构化工具调用。脚本没有执行数据库查询。")
        return 0
    except Exception as exc:
        print(f"响应格式与预期不同：{type(exc).__name__}：{exc}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
