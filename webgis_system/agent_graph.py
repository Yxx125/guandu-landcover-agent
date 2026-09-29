"""LangGraph 工作流：解析→模型选工具→执行→模型回答→核对。

本阶段沿用已通过验证的两个只读工具及 Ollama 服务。
"""

import json
from typing import Any, TypedDict

from fastapi import HTTPException
from langgraph.graph import StateGraph, START, END

from agent_runtime import _expected, _chat
from agent_tools import execute_tool


class AgentState(TypedDict, total=False):
    question: str
    expected_tool: str
    expected_arguments: dict[str, int]
    query: dict[str, Any]
    messages: list[dict[str, Any]]
    tool_result: dict[str, Any]
    model_answer: str
    answer: str
    tool_trace: list[dict[str, Any]]


def parse_question(state: AgentState) -> AgentState:
    name, arguments, query = _expected(state["question"])
    return {
        "expected_tool": name,
        "expected_arguments": arguments,
        "query": query,
        "messages": [
            {"role": "system", "content": "你是官渡区 CLCD 查询助手。必须调用工具获取面积；回答必须引用工具数值，面积保留四位小数。"},
            {"role": "user", "content": state["question"]},
        ],
    }


def select_tool(state: AgentState) -> AgentState:
    reply = _chat(state["messages"])
    calls = reply.get("tool_calls") or []
    if len(calls) != 1:
        raise HTTPException(502, "模型未准确选择一个工具；本次工作流已停止")
    function = calls[0].get("function") or {}
    raw = function.get("arguments", {})
    try:
        arguments = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError) as exc:
        raise HTTPException(502, "模型工具参数不是合法 JSON") from exc
    if function.get("name") != state["expected_tool"] or arguments != state["expected_arguments"]:
        raise HTTPException(502, "模型工具选择与原问题不一致；工作流未执行错误调用")
    return {"messages": state["messages"] + [reply],
            "tool_trace": [{"step": "select_tool", "tool": function["name"],
                            "arguments": arguments}]}


def execute_statistics(state: AgentState) -> AgentState:
    try:
        result = execute_tool(state["expected_tool"], state["expected_arguments"])
    except (ValueError, RuntimeError, KeyError) as exc:
        raise HTTPException(503, f"统计工具执行失败：{exc}") from exc
    messages = state["messages"] + [
        {"role": "tool", "tool_name": state["expected_tool"],
         "content": json.dumps(result, ensure_ascii=False)}]
    return {"tool_result": result, "messages": messages,
            "tool_trace": state["tool_trace"] + [{"step": "execute_statistics",
                                              "source": result["source"]}]}


def generate_answer(state: AgentState) -> AgentState:
    reply = _chat(state["messages"])
    if reply.get("tool_calls"):
        raise HTTPException(502, "模型继续请求工具；本阶段只允许一次统计工具调用")
    answer = str(reply.get("content") or "").strip()
    if not answer:
        raise HTTPException(502, "模型未生成回答")
    return {"model_answer": answer,
            "tool_trace": state["tool_trace"] + [{"step": "generate_answer"}]}


def verify_answer(state: AgentState) -> AgentState:
    result = state["tool_result"]
    if state["expected_tool"] == "get_annual_area":
        row = next((r for r in result["classes"] if r["class_code"] == 1), None)
        if row is None:
            raise HTTPException(503, "年度统计缺少耕地数据")
        area = float(row["area_km2"])
        conclusion = f"官渡区 {state['expected_arguments']['year']} 年耕地面积为 {area:.4f} km²。"
    else:
        row = next((r for r in result["flows"]
                    if r["from_code"] == 1 and r["to_code"] == 8), None)
        area = float(row["area_km2"]) if row else 0.0
        a = state["expected_arguments"]["start_year"]
        b = state["expected_arguments"]["end_year"]
        conclusion = f"官渡区 {a}→{b} 年耕地转建设用地面积为 {area:.4f} km²。"
    if f"{area:.4f}" not in state["model_answer"]:
        raise HTTPException(502, "模型回答与统计工具的四位小数面积不一致")
    return {"answer": f"结论：{conclusion}\n依据：{result['source']}。\n模型回答：{state['model_answer']}",
            "tool_trace": state["tool_trace"] + [
                {"step": "verify_answer", "evidence_area_km2": area, "verified": True}]}


def build_graph():
    builder = StateGraph(AgentState)
    builder.add_node("parse_question", parse_question)
    builder.add_node("select_tool", select_tool)
    builder.add_node("execute_statistics", execute_statistics)
    builder.add_node("generate_answer", generate_answer)
    builder.add_node("verify_answer", verify_answer)
    builder.add_edge(START, "parse_question")
    builder.add_edge("parse_question", "select_tool")
    builder.add_edge("select_tool", "execute_statistics")
    builder.add_edge("execute_statistics", "generate_answer")
    builder.add_edge("generate_answer", "verify_answer")
    builder.add_edge("verify_answer", END)
    return builder.compile()


def answer_with_graph(question: str) -> dict[str, Any]:
    state = build_graph().invoke({"question": question})
    return {"answer": state["answer"], "query": state["query"],
            "tool_trace": state["tool_trace"],
            "model_answer": state["model_answer"],
            "tool_result": state["tool_result"]}
