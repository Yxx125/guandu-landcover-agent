"""官渡区问答 Agent 的只读统计工具。

工具调用由后端校验和执行；模型不能传入 URL、SQL 或 Python 代码。
数据仍由正在运行的 FastAPI 服务及其本地数据库计算。
"""

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen


API_BASE = "http://127.0.0.1:8001"
CLASS_NAMES = {
    1: "耕地", 2: "林地", 3: "灌丛", 4: "草地", 5: "水体",
    6: "积雪／冰川", 7: "裸地", 8: "建设用地（不透水面）", 9: "湿地",
}

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_annual_area",
            "description": "查询官渡区指定年份九类土地覆盖面积和占比。面积单位 km²。",
            "parameters": {
                "type": "object",
                "properties": {"year": {"type": "integer", "description": "1990 至 2025 年"}},
                "required": ["year"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_transition_matrix",
            "description": "比较两个年份的土地覆盖像元，返回九类之间转移面积及变化总面积。起止两年计算，不是相邻年份累计。",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_year": {"type": "integer", "description": "起始年份，1990 至 2024"},
                    "end_year": {"type": "integer", "description": "结束年份，晚于起始年份且不超过 2025"},
                },
                "required": ["start_year", "end_year"],
                "additionalProperties": False,
            },
        },
    },
]


def _tool(name, description, properties, required):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties,
                       "required": list(required), "additionalProperties": False}}}


YEAR = {"type": "integer", "description": "1990–2025 的年份"}
CLASS = {"type": "integer", "description": "CLCD 地类编码 1–9"}
CLASSES = {"type": "array", "items": CLASS, "minItems": 1, "maxItems": 9}
PERIOD = {"start_year": YEAR, "end_year": YEAR}
TOOL_SCHEMAS.extend([
    _tool("compare_years", "比较官渡区两个年份各地类面积和净变化。",
          PERIOD, PERIOD),
    _tool("get_area_timeseries", "查询指定地类从起始年至结束年的逐年面积；供趋势分析。",
          {**PERIOD, "class_codes": CLASSES}, (*PERIOD, "class_codes")),
    _tool("get_yearly_net_change", "查询一种地类每年的面积净增减，不等于转入或转出总量。",
          {**PERIOD, "class_code": CLASS}, (*PERIOD, "class_code")),
    _tool("get_changed_areas", "查询两期之间变化像元、八邻接斑块数量和转入转出面积。",
          {**PERIOD, "min_patch_km2": {"type": "number", "minimum": 0, "maximum": 100}},
          (*PERIOD, "min_patch_km2")),
    _tool("get_point_history", "查询官渡区某经纬度点在指定时段逐年地类与转换。",
          {**PERIOD, "lon": {"type": "number"}, "lat": {"type": "number"}},
          (*PERIOD, "lon", "lat")),
    _tool("get_knowledge_graph", "查询 Neo4j 年度观测与相邻年份地类转换关系。多年边权是相邻年份累计，不是起止两年矩阵。",
          {**PERIOD, "class_codes": CLASSES}, (*PERIOD, "class_codes")),
    _tool("search_documents", "检索已导入的官渡区规划与 CLCD 资料，只返回文档证据，不生成统计数值。",
          {"question": {"type": "string", "minLength": 3, "maxLength": 500}}, ("question",)),
])

# 已通过网页验收的两个工具继续单独暴露给现有试验入口，避免新增工具改变原有示例行为。
CORE_TOOL_SCHEMAS = TOOL_SCHEMAS[:2]


def _parameters(arguments, required):
    if not isinstance(arguments, dict) or set(arguments) != set(required):
        raise ValueError(f"参数只能是：{', '.join(required)}")
    for key in required:
        year = arguments[key]
        if type(year) is not int or not 1990 <= year <= 2025:
            raise ValueError(f"{key} 必须是 1990–2025 的整数")
    return arguments


def _period(arguments, required, allow_equal=False):
    if not isinstance(arguments, dict) or set(arguments) != set(required):
        raise ValueError(f"参数只能是：{', '.join(required)}")
    for key in ("start_year", "end_year"):
        if type(arguments[key]) is not int or not 1990 <= arguments[key] <= 2025:
            raise ValueError(f"{key} 必须是 1990–2025 的整数")
    if (arguments["end_year"] < arguments["start_year"] or
            (not allow_equal and arguments["end_year"] == arguments["start_year"])):
        raise ValueError("结束年份必须晚于起始年份" if not allow_equal else "结束年份不能早于起始年份")
    return arguments


def _classes(codes):
    if (not isinstance(codes, list) or not 1 <= len(codes) <= 9 or
            any(type(code) is not int or code not in CLASS_NAMES for code in codes) or
            len(codes) != len(set(codes))):
        raise ValueError("class_codes 必须是不重复的 1–9 地类编码数组")
    return ",".join(map(str, codes))


def _get(path, params):
    url = API_BASE + path + "?" + urlencode(params)
    try:
        with urlopen(url, timeout=40) as response:
            return json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f"统计接口返回 HTTP {exc.code}；请检查本地数据和后端日志") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("无法连接本地 FastAPI（127.0.0.1:8001），请先启动后端") from exc


def execute_tool(name, arguments):
    """只执行明确登记的只读工具，不执行模型生成的代码或 SQL。"""
    if name == "search_documents":
        if (not isinstance(arguments, dict) or set(arguments) != {"question"} or
                not isinstance(arguments["question"], str) or
                not 3 <= len(arguments["question"].strip()) <= 500):
            raise ValueError("question 必须是 3–500 字的检索问题")
        from app import _retrieve_documents
        documents, status = _retrieve_documents(arguments["question"].strip())
        return {"documents": documents, "retrieval_status": status,
                "source": "官渡区 Chroma 文档向量索引"}
    if name == "get_annual_area":
        args = _parameters(arguments, ("year",))
        raw = _get("/api/stats/annual", args)
        return {
            "year": args["year"], "unit": "km²",
            "classes": [{"class_code": row["class_code"], "class_name": row["class_name"],
                         "area_km2": row["area_km2"], "percentage": row["percentage"]}
                        for row in raw["classes"]],
            "source": "官渡区 CLCD 年度面积表",
        }
    if name == "get_transition_matrix":
        args = _parameters(arguments, ("start_year", "end_year"))
        if args["end_year"] <= args["start_year"]:
            raise ValueError("结束年份必须晚于起始年份")
        raw = _get("/api/stats/transitions", args)
        return {
            **args, "unit": "km²", "changed_area_km2": raw["changed_area_km2"],
            "flows": [{"from_code": row["from_code"], "from_name": row["from_name"],
                       "to_code": row["to_code"], "to_name": row["to_name"],
                       "pixel_count": row["pixel_count"], "area_km2": row["area_km2"]}
                      for row in raw["flows"]],
            "source": "官渡区两期 CLCD 起止年转移矩阵",
        }
    if name == "compare_years":
        args = _period(arguments, ("start_year", "end_year"))
        raw = _get("/api/stats/compare", args)
        return {**args, "unit": "km²", "changes": raw["changes"],
                "source": "官渡区 CLCD 起止年面积比较"}
    if name in {"get_area_timeseries", "get_yearly_net_change", "get_knowledge_graph"}:
        key = "class_code" if name == "get_yearly_net_change" else "class_codes"
        args = _period(arguments, ("start_year", "end_year", key),
                       allow_equal=name in {"get_area_timeseries", "get_knowledge_graph"})
        if key == "class_code":
            if type(args[key]) is not int or args[key] not in CLASS_NAMES:
                raise ValueError("class_code 必须是 1–9 的整数")
            params = args
        else:
            params = {**args, "class_codes": _classes(args["class_codes"])}
        path = {"get_area_timeseries": "/api/stats/timeseries",
                "get_yearly_net_change": "/api/stats/net-change",
                "get_knowledge_graph": "/api/graph"}[name]
        raw = _get(path, params)
        if name == "get_knowledge_graph":
            return {"scope": raw["scope"], "unit": raw["unit"],
                    "note": raw["note"], "nodes": raw["nodes"], "links": raw["links"],
                    "source": "官渡区 Neo4j 年度观测关系图"}
        return {**raw, "source": "官渡区 CLCD 年度统计表"}
    if name == "get_changed_areas":
        args = _period(arguments, ("start_year", "end_year", "min_patch_km2"))
        threshold = args["min_patch_km2"]
        if type(threshold) not in (int, float) or not 0 <= threshold <= 100:
            raise ValueError("min_patch_km2 必须是 0–100 的数值")
        raw = _get("/api/stats/changed-areas", args)
        return {**raw, "source": "官渡区两期 CLCD 八邻接连续变化斑块统计"}
    if name == "get_point_history":
        args = _period(arguments, ("start_year", "end_year", "lon", "lat"))
        if (type(args["lon"]) not in (int, float) or not -180 <= args["lon"] <= 180 or
                type(args["lat"]) not in (int, float) or not -90 <= args["lat"] <= 90):
            raise ValueError("lon/lat 必须是合法经纬度数值")
        raw = _get("/api/point-history", args)
        return {**raw, "source": "官渡区逐年 CLCD 原始栅格点位查询"}
    raise ValueError("未登记的工具：" + str(name))
