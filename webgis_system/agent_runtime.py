"""官渡区两项统计工具的 Ollama Function Calling 网页试验入口。"""

import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import HTTPException
from agent_tools import CORE_TOOL_SCHEMAS, execute_tool


OLLAMA_URL = os.environ.get("OLLAMA_CHAT_URL", "http://127.0.0.1:11434/api/chat")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")


def _expected(question):
    years = [int(s) for s in re.findall(r"(?<!\d)(?:19\d{2}|20[0-2]\d)(?!\d)", question)]
    if "耕地" not in question:
        raise HTTPException(400, "工具调用试验目前只支持明确提到耕地的问题；其他问题请使用原问答模式")
    if len(years) == 1 and ("面积" in question or "多少" in question):
        return "get_annual_area", {"year": years[0]}, {
            "start_year": years[0], "end_year": years[0],
            "class_codes": [1], "chart_mode": "annual", "min_patch_km2": 0,
        }
    if (len(years) == 2 and years[0] < years[1] and
            ("转建设用地" in question or "转为建设用地" in question or "转成建设用地" in question)):
        return "get_transition_matrix", {"start_year": years[0], "end_year": years[1]}, {
            "start_year": years[0], "end_year": years[1],
            "class_codes": [1, 8], "chart_mode": "transition", "min_patch_km2": 0,
        }
    raise HTTPException(400, "工具调用试验目前支持单年耕地面积或两年耕地转建设用地；其他问题请使用原问答模式")


def _chat(messages, *, tools=True):
    from model_gateway import chat
    return chat(messages, tools=CORE_TOOL_SCHEMAS if tools else None)


def answer_with_tools(question):
    name, parameters, query = _expected(question)
    messages = [
        {"role": "system", "content": "你是官渡区 CLCD 查询助手。必须调用工具查询，面积保留四位小数，不要猜测。"},
        {"role": "user", "content": question},
    ]
    first = _chat(messages)
    calls = first.get("tool_calls") or []
    if len(calls) != 1:
        raise HTTPException(502, "模型没有准确选择一个统计工具；请重试或切回原问答模式")
    function = calls[0].get("function") or {}
    raw = function.get("arguments", {})
    try:
        arguments = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError) as exc:
        raise HTTPException(502, "模型返回的工具参数不是合法 JSON") from exc
    if function.get("name") != name or arguments != parameters:
        raise HTTPException(502, "模型选择的工具或年份与原问题不一致；未执行错误调用")
    try:
        result = execute_tool(name, arguments)
    except (ValueError, RuntimeError, KeyError) as exc:
        raise HTTPException(503, f"本地统计工具未就绪：{exc}") from exc

    if name == "get_annual_area":
        row = next((r for r in result["classes"] if r["class_code"] == 1), None)
        if row is None:
            raise HTTPException(503, "年度统计未返回耕地数据")
        area = float(row["area_km2"])
        conclusion = f"官渡区 {parameters['year']} 年耕地面积为 {area:.4f} km²。"
    else:
        row = next((r for r in result["flows"]
                    if r["from_code"] == 1 and r["to_code"] == 8), None)
        area = float(row["area_km2"]) if row else 0.0
        conclusion = (f"官渡区 {parameters['start_year']}→{parameters['end_year']} 年"
                      f"耕地转建设用地面积为 {area:.4f} km²。")
    from model_gateway import mode_name
    if mode_name() == "cloud":
        call_id = calls[0].get("id")
        if not call_id:
            raise HTTPException(502, "云端工具调用缺少 tool_call_id")
        tool_reply = {"role": "tool", "tool_call_id": call_id,
                      "content": json.dumps(result, ensure_ascii=False)}
    else:
        tool_reply = {"role": "tool", "tool_name": name,
                      "content": json.dumps(result, ensure_ascii=False)}
    messages.extend([first, tool_reply])
    final = _chat(messages, tools=False)
    if final.get("tool_calls"):
        raise HTTPException(502, "模型继续请求额外工具，试验入口暂不执行第二次调用")
    generated = str(final.get("content") or "").strip()
    if f"{area:.4f}" not in generated:
        raise HTTPException(502, "模型回答未包含统计工具的准确面积；已拒绝不一致回答")
    return {
        "answer": f"结论：{conclusion}\n依据：{result['source']}。\n模型回答：{generated}",
        "query": query, "facts": [row] if row else [],
        "evidence": {"documents": [], "retrieval_status": "not_needed",
                     "statistics": result["source"]},
        "generation_mode": "agent_" + mode_name(),
        "tool_trace": [{"tool": name, "arguments": arguments,
                        "evidence_area_km2": area, "unit": "km²"}],
    }
