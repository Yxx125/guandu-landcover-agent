"""最多三次只读工具调用的 LangGraph Agent；数值始终来自已校验工具。"""

import json
import re
from typing import Any, TypedDict

from fastapi import HTTPException
from langgraph.graph import END, START, StateGraph

from agent_graph_full import check_arguments, summarize
from agent_tools import CLASS_NAMES, TOOL_SCHEMAS, execute_tool
from model_gateway import chat, mode_name


class MultiState(TypedDict, total=False):
    question: str
    messages: list[dict[str, Any]]
    pending: dict[str, Any] | None
    receipts: list[dict[str, Any]]
    trace: list[dict[str, Any]]
    answer: str
    required: list[dict[str, Any]]


def required_operations(question: str) -> list[dict[str, Any]]:
    """识别明确的单年面积 + 两年面积净变化组合；不推断未给出的年份。"""
    years = [int(y) for y in re.findall(r"(?<!\d)(?:19\d{2}|20[0-2]\d)(?!\d)", question)]
    if (len(years) == 3 and years[0] == years[2] and years[1] < years[2]
            and "面积" in question and any(x in question for x in ("净变化", "净增减"))
            and not any(x in question for x in ("逐年", "每年"))):
        return [{"name": "get_annual_area", "arguments": {"year": years[0]}},
                {"name": "compare_years", "arguments": {
                    "start_year": years[1], "end_year": years[2]}}]
    return []


def prepare(state: MultiState) -> dict:
    question = state["question"].strip()
    if not 3 <= len(question) <= 500:
        raise HTTPException(400, "问题长度须为 3–500 字")
    return {"receipts": [], "trace": [], "pending": None,
            "required": required_operations(question), "messages": [
        {"role": "system", "content": (
            "你是官渡区土地覆盖分析助手。此问题含至少两个独立查询指标。"
            "每次只调用一个已登记的只读工具，再查看真实工具返回值，决定下一步；"
            "完成至少两次且最多三次不同的工具查询后，输出 DONE，不要自己计算或编造数字。"
            "只能使用用户明示的年份、地类、坐标及阈值。地类编码："
            "1耕地、2林地、3灌丛、4草地、5水体、6积雪冰川、7裸地、8建设用地、9湿地。")},
        {"role": "user", "content": question},
    ]}


def decide(state: MultiState) -> dict:
    remaining = [x for x in state.get("required", [])
                 if not any(r["name"] == x["name"] and r["arguments"] == x["arguments"]
                            for r in state["receipts"])]
    if state.get("required") and not remaining:
        return {"pending": None, "trace": state["trace"] + [{"step": "finish"}]}
    schemas = ([schema for schema in TOOL_SCHEMAS
                if schema["function"]["name"] == remaining[0]["name"]]
               if remaining else TOOL_SCHEMAS)
    # 每一步都先交给模型选工具；模型不返回有效调用时才记录规则回退。
    # 必需指标尚未查询时，明确要求模型调用当前唯一的工具。
    # OpenAI 兼容服务支持标准 named function choice；本地 Ollama 使用 required。
    tool_choice = ({"type": "function", "function": {"name": remaining[0]["name"]}}
                   if remaining and mode_name() == "cloud" else
                   "required" if remaining else "auto")
    reply = chat(state["messages"], tools=schemas, tool_choice=tool_choice)
    calls = reply.get("tool_calls") or []
    policy = False
    fallback_reason = None
    ignored_calls = 0
    if remaining and len(calls) > 1:
        # 中转服务可能无视 named tool_choice 而同轮返回多个调用。
        # 只接受唯一一个名称和参数都匹配当前必需查询的调用，不能执行其他调用。
        matching = []
        for call in calls:
            fn = call.get("function") or {}
            raw = fn.get("arguments")
            try:
                candidate_args = json.loads(raw) if isinstance(raw, str) else raw
            except (ValueError, TypeError):
                continue
            if (fn.get("name") == remaining[0]["name"] and
                    isinstance(candidate_args, dict) and
                    (candidate_args == remaining[0]["arguments"] or
                     fn.get("name") == "get_annual_area" and
                     candidate_args.get("year") == remaining[0]["arguments"]["year"])):
                matching.append(call)
        if len(matching) == 1:
            ignored_calls = len(calls) - 1
            calls = matching
            reply = {**reply, "tool_calls": calls}
        else:
            fallback_reason = "multiple_calls_no_unique_match"
            calls = []
    if remaining and (len(calls) != 1 or
                      (calls[0].get("function") or {}).get("name") != remaining[0]["name"]):
        fallback_reason = fallback_reason or ("no_tool_calls" if not calls else "wrong_tool")
        calls = []
    if not calls and remaining:
        # 可记录的确定性回退：模型未完成工具协议时仍按问题明确给出的两项执行。
        name, args = remaining[0]["name"], remaining[0]["arguments"]
        reply, policy = None, True
    elif calls:
        fn = calls[0].get("function") or {}
        name, raw = fn.get("name"), fn.get("arguments")
        if name not in {tool["function"]["name"] for tool in schemas}:
            raise HTTPException(422, "模型请求了未登记工具")
        try:
            args = json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, "工具参数不是合法 JSON") from exc
        if not isinstance(args, dict):
            raise HTTPException(422, "工具参数必须为对象")
        if name == "get_annual_area":
            args = {key: value for key, value in args.items()
                    if key not in {"class_code", "class_codes"}}
        if remaining and args != remaining[0]["arguments"]:
            fallback_reason = "arguments_mismatch"
            name, args, reply, policy = remaining[0]["name"], remaining[0]["arguments"], None, True
        if remaining and mode_name() == "cloud" and not calls[0].get("id"):
            fallback_reason = "missing_tool_call_id"
            name, args, reply, policy = remaining[0]["name"], remaining[0]["arguments"], None, True
    if not calls:
        if not policy and len(state["receipts"]) < 2:
            raise HTTPException(422, "模型未完成两个不同的工具查询；未生成组合答案")
        if not policy:
            return {"pending": None, "trace": state["trace"] + [{"step": "finish"}]}
    if len(calls) != 1 or len(state["receipts"]) >= 3:
        if not policy:
            raise HTTPException(422, "单轮只允许一个工具，整次最多三个工具")
    if any(item["name"] == name and item["arguments"] == args for item in state["receipts"]):
        raise HTTPException(422, "模型重复请求完全相同的工具与参数")
    validation_question = state["question"]
    if "start_year" in args and "end_year" in args:
        # 多指标问题的年份可能按“单年、起始年、结束年”排列。
        without_years = re.sub(r"(?<!\d)(?:19\d{2}|20[0-2]\d)(?!\d)", "", validation_question)
        validation_question = f"{args['start_year']}到{args['end_year']} " + without_years
    check_arguments({"question": validation_question, "name": name,
                     "arguments": args, "trace": []})
    return {"pending": {"name": name, "arguments": args, "assistant": reply,
                        "call_id": calls[0].get("id") if not policy else None,
                        "policy": policy},
            "trace": state["trace"] + [{"step": "policy_select" if policy else "choose",
                                         "tool": name, "arguments": args,
                                         **({"ignored_tool_calls": ignored_calls} if ignored_calls else {}),
                                         **({"reason": fallback_reason} if policy else {})}]}


def run(state: MultiState) -> dict:
    pending = state["pending"]
    name, args = pending["name"], pending["arguments"]
    try:
        result = execute_tool(name, args)
    except (ValueError, RuntimeError, KeyError, TypeError) as exc:
        raise HTTPException(422, f"工具参数或本地数据错误：{exc}") from exc
    summary = summarize({"question": state["question"], "name": name,
                         "arguments": args, "result": result, "trace": []})["answer"]
    if pending["policy"]:
        tool_message = {"role": "user", "content": "已从本地统计工具核验的结果：" +
                        json.dumps(result, ensure_ascii=False)}
    elif mode_name() == "cloud":
        if not pending["call_id"]:
            raise HTTPException(502, "云端工具调用缺少 tool_call_id")
        tool_message = {"role": "tool", "tool_call_id": pending["call_id"],
                        "content": json.dumps(result, ensure_ascii=False)}
    else:
        tool_message = {"role": "tool", "tool_name": name,
                        "content": json.dumps(result, ensure_ascii=False)}
    receipts = state["receipts"] + [{"name": name, "arguments": args,
                                     "source": result["source"], "summary": summary}]
    # 第三次执行后直接收束，避免再次请求模型产生第四次工具调用。
    return {"receipts": receipts,
            "messages": state["messages"] + ([pending["assistant"]] if pending["assistant"] else []) + [tool_message],
            "trace": state["trace"] + [{"step": "run_tool", "tool": name,
                                         "source": result["source"]}]}


def finish(state: MultiState) -> dict:
    if len(state["receipts"]) < 2:
        raise HTTPException(422, "组合问题至少需要两个独立的查询结果")
    # 程序原样拼接各工具的确定性结论，不请模型改写数值。
    return {"answer": "\n".join(item["summary"] for item in state["receipts"])}


def route_after_decide(state: MultiState) -> str:
    return "run" if state["pending"] is not None else "finish"


def route_after_run(state: MultiState) -> str:
    return "finish" if len(state["receipts"]) >= 3 else "decide"


def build_multi_graph():
    graph = StateGraph(MultiState)
    for name, fn in (("prepare", prepare), ("decide", decide),
                     ("run", run), ("finish", finish)):
        graph.add_node(name, fn)
    graph.add_edge(START, "prepare")
    graph.add_edge("prepare", "decide")
    graph.add_conditional_edges("decide", route_after_decide,
                                {"run": "run", "finish": "finish"})
    graph.add_conditional_edges("run", route_after_run,
                                {"decide": "decide", "finish": "finish"})
    graph.add_edge("finish", END)
    return graph.compile()


def answer_multi(question: str) -> dict:
    years = [int(y) for y in re.findall(r"(?<!\d)(?:19\d{2}|20[0-2]\d)(?!\d)", question)]
    if not years or len(set(years)) > 2:
        raise HTTPException(422, "组合查询请明确一个年份或一个起止年份区间")
    state = build_multi_graph().invoke({"question": question})
    aliases = {8: ("建设用地", "不透水面"), 5: ("水域", "水体")}
    codes = [code for code, label in CLASS_NAMES.items()
             if label in question or any(alias in question for alias in aliases.get(code, ()))]
    start, end = min(years), max(years)
    return {"answer": state["answer"], "tool": "multi_tool",
            "tool_trace": state["trace"], "tool_results": state["receipts"],
            "generation_mode": "agent_multi_" + ("policy" if any(
                t["step"] == "policy_select" for t in state["trace"]) else mode_name()),
            "query": {"start_year": start, "end_year": end,
                      "class_codes": codes or list(CLASS_NAMES),
                      "chart_mode": "annual" if start == end else "timeseries",
                      "min_patch_km2": 0},
            "evidence": {"statistics": [r["source"] for r in state["receipts"]],
                         "documents": [], "retrieval_status": "not_needed"}}
