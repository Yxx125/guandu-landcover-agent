"""第 06 步：八个只读工具的 LangGraph 工作流（独立于现有网页试验入口）。"""

import json
import re
from typing import Any, TypedDict

from fastapi import HTTPException
from langgraph.graph import END, START, StateGraph

from agent_tools import CLASS_NAMES, TOOL_SCHEMAS, execute_tool


class FullState(TypedDict, total=False):
    question: str
    messages: list[dict[str, Any]]
    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    answer: str
    trace: list[dict[str, Any]]


def prepare(state: FullState) -> FullState:
    question = state["question"].strip()
    if not 3 <= len(question) <= 500:
        raise HTTPException(400, "问题长度须为 3–500 字")
    return {"messages": [
        {"role": "system", "content": (
            "你是官渡区 CLCD 工具路由器。根据用户问题只选择一个最贴切的工具，"
            "不要自行回答、不要编造参数。年份仅限 1990–2025。"
            "比较起止两年地类流转选 get_transition_matrix；两年各地类面积对比选 compare_years；"
            "单年面积选 get_annual_area；逐年面积趋势选 get_area_timeseries；"
            "逐年净增减选 get_yearly_net_change；变化斑块/阈值筛选选 get_changed_areas；"
            "经纬度点位历史选 get_point_history；知识图谱关系选 get_knowledge_graph；"
            "规划资料或数据来源选 search_documents。"
            "地类编码：1耕地，2林地，3灌丛，4草地，5水体，6积雪冰川，7裸地，8建设用地，9湿地。"
            "只有用户明确给出经纬度时才能选择点位工具。没有必要参数时不要猜测。")},
        {"role": "user", "content": question}], "trace": [{"step": "prepare"}]}


def choose(state: FullState) -> FullState:
    from model_gateway import chat
    reply = chat(state["messages"], tools=TOOL_SCHEMAS)
    calls = reply.get("tool_calls") or []
    if len(calls) != 1:
        raise HTTPException(422, "本阶段要求模型恰好选择一个工具；请把问题拆成单项查询")
    fn = calls[0].get("function") or {}
    raw = fn.get("arguments")
    try:
        args = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, "工具参数不是合法 JSON") from exc
    if fn.get("name") not in {x["function"]["name"] for x in TOOL_SCHEMAS} or not isinstance(args, dict):
        raise HTTPException(422, "模型选择了未登记的工具或参数格式错误")
    # Ollama 偶尔为单年面积额外填入 class_code/class_codes。
    # 年度面积接口本来返回九类；具体地类在回答节点从结果中筛选。
    # 仅剔除这两个冗余字段，其他未知参数仍交给工具校验拒绝。
    if fn["name"] == "get_annual_area":
        args = {key: value for key, value in args.items()
                if key not in {"class_code", "class_codes"}}
    return {"name": fn["name"], "arguments": args,
            "trace": state["trace"] + [{"step": "choose", "tool": fn["name"], "arguments": args}]}


def check_arguments(state: FullState) -> FullState:
    args = state["arguments"]
    if state["name"] == "search_documents":
        if args.get("question") != state["question"]:
            raise HTTPException(422, "检索问题必须与用户原问题一致")
        return {"trace": state["trace"] + [{"step": "check_arguments", "verified": True}]}
    years = [int(x) for x in re.findall(r"(?<!\d)(?:19\d{2}|20[0-2]\d)(?!\d)", state["question"])]
    requested_years = set(years)
    supplied_years = {v for k, v in args.items() if k in {"year", "start_year", "end_year"} and type(v) is int}
    if not requested_years or not supplied_years.issubset(requested_years):
        raise HTTPException(422, "模型给出的年份不在原问题中；未执行工具")
    if len(requested_years) >= 2 and {"start_year", "end_year"}.issubset(args):
        if args["start_year"] != years[0] or args["end_year"] != years[1]:
            raise HTTPException(422, "起止年份与原问题顺序不一致")
    if len(requested_years) == 1 and {"start_year", "end_year"}.issubset(args):
        if args["start_year"] != years[0] or args["end_year"] != years[0]:
            raise HTTPException(422, "模型猜测了问题未给出的第二个年份")
    for key in ("class_code", "class_codes"):
        if key in args:
            codes = args[key] if isinstance(args[key], list) else [args[key]]
            for code in codes:
                aliases = {"建设用地" if code == 8 else CLASS_NAMES.get(code, "")}
                if code == 8: aliases.add("不透水面")
                if code == 5: aliases.update(("水域", "水体"))
                if code == 7: aliases.add("荒地")
                if not any(alias and alias in state["question"] for alias in aliases):
                    raise HTTPException(422, f"问题未指定地类 {code}；未执行模型猜测的参数")
    if state["name"] == "get_point_history":
        numbers = re.findall(r"(?<!\d)-?\d{2,3}\.\d+(?!\d)", state["question"])
        if len(numbers) < 2 or not all(any(abs(float(s) - float(args[k])) < 1e-5 for s in numbers)
                                         for k in ("lon", "lat")):
            raise HTTPException(422, "问题未给出匹配的经纬度")
    if state["name"] == "get_changed_areas" and "min_patch_km2" in args:
        number = args["min_patch_km2"]
        if number != 0 and not any(abs(float(s)-float(number)) < 1e-6 for s in
                                   re.findall(r"\d+(?:\.\d+)?", state["question"])):
            raise HTTPException(422, "变化斑块阈值不在原问题中")
    return {"trace": state["trace"] + [{"step": "check_arguments", "verified": True}]}


def run_tool(state: FullState) -> FullState:
    try:
        result = execute_tool(state["name"], state["arguments"])
    except (ValueError, RuntimeError, KeyError, TypeError) as exc:
        raise HTTPException(422, f"工具参数或数据有误：{exc}") from exc
    return {"result": result,
            "trace": state["trace"] + [{"step": "run_tool", "source": result["source"]}]}


def summarize(state: FullState) -> FullState:
    """由工具结果生成关键回答；不让模型改写统计数值。"""
    name, a, r = state["name"], state["arguments"], state["result"]
    period = f"{a.get('start_year')}→{a.get('end_year')} 年"
    if name == "get_annual_area":
        rows = [x for x in r["classes"] if x["class_name"] in state["question"] or
                (x["class_code"] == 8 and "建设用地" in state["question"])]
        if not rows:
            rows = r["classes"]
        body = "；".join(f"{x['class_name']} {float(x['area_km2']):.4f} km²" for x in rows)
        lead = f"官渡区 {a['year']} 年土地覆盖面积：{body}。"
    elif name == "get_transition_matrix":
        rows = r["flows"]
        selected = [x for x in rows if x["from_name"] in state["question"] and
                    (x["to_name"] in state["question"] or
                     (x["to_code"] == 8 and "建设用地" in state["question"]))]
        if selected:
            rows = selected
        detail = "；".join(
            f"{x['from_name']}→{x['to_name']} {float(x['area_km2']):.4f} km²"
            for x in sorted(rows, key=lambda x: x['area_km2'], reverse=True)[:5])
        if selected:
            lead = f"官渡区 {period}{detail}。全区变化面积 {float(r['changed_area_km2']):.4f} km²。"
        else:
            lead = (f"官渡区 {period}变化面积 {float(r['changed_area_km2']):.4f} km²；"
                    f"主要转移：{detail}。")
    elif name == "compare_years":
        selected = [x for x in r["changes"] if x["class_name"] in state["question"] or
                    (x["class_code"] == 8 and "建设用地" in state["question"])]
        lead = f"官渡区 {period}各类面积净变化：" + "；".join(
            f"{x['class_name']} {float(x['net_change_km2']):+.4f} km²"
            for x in (selected or r["changes"])) + "。"
    elif name == "get_area_timeseries":
        lead = f"官渡区 {period}逐年面积：" + "；".join(
            x["class_name"] + " " + "、".join(
                f"{p['year']}年 {float(p['area_km2']):.4f}" for p in x["data"])
            for x in r["series"]) + " km²。"
    elif name == "get_yearly_net_change":
        lead = f"官渡区 {period}{CLASS_NAMES[a['class_code']]}逐年净变化：" + "；".join(
            f"{x['year']}年 {float(x['net_change_km2']):+.4f} km²" for x in r["data"]) + "。"
    elif name == "get_changed_areas":
        patches = r.get("patch_count")
        lead = (f"官渡区 {period}阈值 {a['min_patch_km2']} km²，"
                f"变化面积 {float(r['changed_area_km2']):.4f} km²；"
                f"变化像元 {r['changed_pixel_count']} 个；"
                f"变化斑块 {'未计算' if patches is None else str(patches)+' 个'}。")
    elif name == "get_point_history":
        lead = (f"坐标（{a['lon']}, {a['lat']}）{period}地类历史：" + "；".join(
            f"{x['year']}年 {x['class_name']}" for x in r["history"]) +
            f"；转变 {r['change_count']} 次。")
    elif name == "search_documents":
        snippets = r["documents"][:3]
        lead = ("检索到的资料片段：" + "；".join(
            f"{item['source']}：{item['text'][:160]}" for item in snippets) + "。"
            if snippets else "当前没有检索到可用资料片段。")
    else:
        lead = (f"官渡区 {period}知识图谱返回 {len(r['nodes'])} 个节点、"
                f"{len(r['links'])} 条关系。说明：{r['note']}")
    return {"answer": f"结论：{lead}\n依据：{r['source']}。",
            "trace": state["trace"] + [{"step": "summarize", "verified": True}]}


def build_full_graph():
    graph = StateGraph(FullState)
    for name, fn in (("prepare", prepare), ("choose", choose),
                     ("check_arguments", check_arguments), ("run_tool", run_tool),
                     ("summarize", summarize)):
        graph.add_node(name, fn)
    graph.add_edge(START, "prepare")
    graph.add_edge("prepare", "choose")
    graph.add_edge("choose", "check_arguments")
    graph.add_edge("check_arguments", "run_tool")
    graph.add_edge("run_tool", "summarize")
    graph.add_edge("summarize", END)
    return graph.compile()


def parse_periods(state: FullState) -> FullState:
    question = state["question"]
    years = [int(x) for x in re.findall(r"(?<!\d)(?:19\d{2}|20[0-2]\d)(?!\d)", question)]
    if len(years) != 4 or not all(1990 <= y <= 2025 for y in years):
        raise HTTPException(400, "请明确两个区间各自的起止年份")
    a, b, c, d = years
    if a >= b or c >= d:
        raise HTTPException(400, "两个区间均须按起始年到结束年填写")
    names = {code: label for code, label in CLASS_NAMES.items() if label in question}
    if "建设用地" in question:
        names[8] = CLASS_NAMES[8]
    if len(names) != 1:
        raise HTTPException(400, "此类区间对比请只指定一种地类")
    metric = "outgoing" if "转出" in question else "incoming" if "转入" in question else None
    if metric is None or ("转出" in question and "转入" in question):
        raise HTTPException(400, "请明确比较转出还是转入")
    return {"name": "compare_transition_periods",
            "arguments": {"periods": [[a, b], [c, d]], "class_code": next(iter(names)),
                          "metric": metric},
            "trace": [{"step": "parse_periods", "verified": True}]}


def execute_periods(state: FullState) -> FullState:
    args = state["arguments"]
    rows = []
    for start, end in args["periods"]:
        try:
            matrix = execute_tool("get_transition_matrix",
                                  {"start_year": start, "end_year": end})
        except (ValueError, RuntimeError, KeyError, TypeError) as exc:
            raise HTTPException(503, f"区间转移矩阵查询失败：{exc}") from exc
        key = "from_code" if args["metric"] == "outgoing" else "to_code"
        pixels = sum(int(flow["pixel_count"]) for flow in matrix["flows"]
                     if flow[key] == args["class_code"] and
                     flow["from_code"] != flow["to_code"])
        rows.append({"start_year": start, "end_year": end,
                     "pixel_count": pixels, "area_km2": round(pixels * .0009, 4)})
    return {"result": {"periods": rows, "source": "官渡区两期 CLCD 起止年转移矩阵"},
            "trace": state["trace"] + [
                {"step": "get_transition_matrix", "period": args["periods"][0]},
                {"step": "get_transition_matrix", "period": args["periods"][1]}]}


def compare_periods(state: FullState) -> FullState:
    first, second = state["result"]["periods"]
    args = state["arguments"]
    label = f"{CLASS_NAMES[args['class_code']]}{'转出' if args['metric'] == 'outgoing' else '转入'}"
    if first["pixel_count"] == second["pixel_count"]:
        conclusion = "两个区间相同"
    else:
        winner = first if first["pixel_count"] > second["pixel_count"] else second
        conclusion = f"{winner['start_year']}→{winner['end_year']} 年更多"
    diff = abs(first["pixel_count"] - second["pixel_count"]) * .0009
    answer = (f"结论：官渡区{label}，{conclusion}。\n依据："
              f"{first['start_year']}→{first['end_year']} 年 {first['area_km2']:.4f} km²；"
              f"{second['start_year']}→{second['end_year']} 年 {second['area_km2']:.4f} km²；"
              f"相差 {diff:.4f} km²。分别按各区间起止年的转移矩阵计算。")
    return {"answer": answer,
            "trace": state["trace"] + [{"step": "compare_periods", "verified": True}]}


def build_period_graph():
    graph = StateGraph(FullState)
    graph.add_node("parse_periods", parse_periods)
    graph.add_node("execute_periods", execute_periods)
    graph.add_node("compare_periods", compare_periods)
    graph.add_edge(START, "parse_periods")
    graph.add_edge("parse_periods", "execute_periods")
    graph.add_edge("execute_periods", "compare_periods")
    graph.add_edge("compare_periods", END)
    return graph.compile()


def answer_with_full_graph(question: str) -> dict[str, Any]:
    years = re.findall(r"(?<!\d)(?:19\d{2}|20[0-2]\d)(?!\d)", question)
    if len(years) == 4 and ("转出" in question or "转入" in question):
        state = build_period_graph().invoke({"question": question})
        periods = state["result"]["periods"]
        code = state["arguments"]["class_code"]
        return {"answer": state["answer"], "tool": state["name"],
                "arguments": state["arguments"], "tool_trace": state["trace"],
                "tool_result": state["result"],
                "query": {"start_year": periods[0]["start_year"],
                          "end_year": max(row["end_year"] for row in periods),
                          "class_codes": [code], "chart_mode": "periodcompare",
                          "comparison_periods": periods,
                          "comparison_label": f"{CLASS_NAMES[code]}{'转出' if state['arguments']['metric'] == 'outgoing' else '转入'}",
                          "min_patch_km2": 0}}
    state = build_full_graph().invoke({"question": question})
    return {"answer": state["answer"], "tool": state["name"],
            "arguments": state["arguments"], "tool_trace": state["trace"],
            "tool_result": state["result"]}
