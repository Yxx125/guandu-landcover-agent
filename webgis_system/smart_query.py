"""自然语言查询：模型仅规划，所有数字由本地已核验接口计算。

模型不会生成 SQL、Python 或面积数值。新增问法应先复用现有分析操作；
没有对应数据或问题含糊时返回说明，不假装已经回答。
"""

import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import HTTPException
from config import USE_LOCAL_OLLAMA


CLASS_NAMES = {
    1: "耕地", 2: "林地", 3: "灌丛", 4: "草地", 5: "水体",
    6: "积雪／冰川", 7: "裸地", 8: "建设用地（不透水面）", 9: "湿地",
}
INTENTS = {"annual", "compare", "trend", "net_change", "transition",
           "changed_areas", "extreme_interval", "extreme_year",
           "extreme_transition_interval",
           "class_rank", "presence", "period_compare", "document", "clarify",
           "flow_compare", "transition_destination_rank", "patch_count", "causal_assessment"}
SYSTEM_PROMPT = """你是官渡区 CLCD 土地覆盖查询规划器。只输出一个 JSON 对象，不回答数值，不生成 SQL。
字段：intent、start_year、end_year、classes、from_class、to_class、extreme、measure、explain、clarification。
intent 只能为 annual（单年面积/比例）、compare（两年比较）、trend（逐年面积）、net_change（逐年净变化）、transition（起止年转移矩阵）、changed_areas（变化区域转入转出）、extreme_interval（某地类哪个相邻年份区间面积变化最大/最小）、extreme_year（某地类哪个年份面积最大/最小）、extreme_transition_interval（某地类转为另一地类在哪个相邻年份区间转移面积最大/最小）、class_rank（某一年最大/最小地类）、presence（有没有某地类，可检查一年的或全时段是否出现）、period_compare（两个不同时间段的转出/转入/地类转换/净变化对比）、document（来源/背景/依据等文档问题）、clarify（条件不清或现有数据无法计算）。
年份为数字或 null。用户没有给年份时：逐年趋势、逐年转移极值、面积极值与净变化可用 1990 至 2025；单年面积、指定两年转移与比较不能猜年份，设 clarify。
classes 是 1–9 的编码数组；耕地1 林地2 灌丛3 草地4 水体5 积雪冰川6 裸地7 建设用地/不透水面8 湿地9。未指定地类用 []。
from_class/to_class 仅在明确问 A 到 B 的转移时填写，否则 null。extreme 取 max/min/null；measure 取 absolute/increase/decrease/null，未说明的“变化最大”取 absolute，表示两年面积差的绝对值。
“哪一年到哪一年的耕地变化最大”是 extreme_interval，classes=[1]，范围 1990 至 2025。用户给两个年份的“转化”是 transition；“每年净增加多少”是 net_change；问变化原因不能凭 CLCD 证明因果，只能依文档谨慎解释。
“哪一年的耕地转为建设用地最多”是 extreme_transition_interval，from_class=1、to_class=8，未指定时间范围时比较 1990→1991 至 2024→2025 全部相邻年份；不能设 clarify。必须区分这种逐年转移极值与指定起止两年的 transition。
“这个区域内有冰川吗”是 presence，classes=[6]。CLCD 类别 6 是积雪／冰川的合并类别，不能把检测到该类直接当作冰川存在的证明；没有指定年份时查 1990–2025 所有年份。
“2011到2012年的耕地转出更多还是2023到2025年的耕地转出更多”是 period_compare，四个年份构成两个起止年区间，不是年份超限。每个区间独立计算起止年转移矩阵中耕地转为其他地类的面积，不按每年净变化求和。
同一时间段两种转移方向比较用 flow_compare；某地类主要转成哪类用 transition_destination_rank；问面积阈值以上变化斑块数量用 patch_count；问耕地减少是否由城市扩张造成用 causal_assessment，只能给转移和净变化证据，不能从地类转换断言因果。
只识别用户给定的年份和地类，不能自行补造年份、具体数据或街道数据。若条件不全，clarification 用一句中文说缺什么；其他情形设空字符串。explain 是布尔值，询问为什么、资料、说明时为 true。
只输出 JSON，字段齐全。"""


def _model_json(question, feedback=None):
    payload = {"model": os.environ.get("OLLAMA_MODEL", "qwen2.5:7b") if USE_LOCAL_OLLAMA
               else os.environ.get("MODEL_NAME", "gpt-5.6-sol"),
               "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": question if not feedback else
                             f"问题：{question}\n上次规划无法执行：{feedback}。请重新判断是不是询问所有年份中的极值；若是，请选择对应的 extreme 操作，不要要求用户给出待求的年份。"}]}
    if USE_LOCAL_OLLAMA:
        url = os.environ.get("OLLAMA_CHAT_URL", "http://127.0.0.1:11434/api/chat")
        payload.update(stream=False, format="json")
        headers = {"Content-Type": "application/json"}
        mode = "ollama"
    else:
        base = os.environ.get("MODEL_API_BASE_URL", "").rstrip("/")
        key = os.environ.get("MODEL_API_KEY", "")
        if not base or not key:
            raise HTTPException(503, "问答规划模型未配置，请检查模型地址和密钥")
        url = base + "/chat/completions"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
        mode = "cloud"
    try:
        request = Request(url, data=json.dumps(payload).encode("utf-8"),
                          headers=headers, method="POST")
        # 本地模型首次载入内存可能明显慢于后续请求。
        # 超时仅控制等待，不改变模型结果或统计口径。
        with urlopen(request, timeout=120 if USE_LOCAL_OLLAMA else 60) as response:
            data = json.load(response)
        content = (data["message"]["content"] if USE_LOCAL_OLLAMA
                   else data["choices"][0]["message"]["content"])
        content = content.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(content), mode
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"问答规划失败：{type(exc).__name__}: {exc}")
        raise HTTPException(503, "智能问答规划未就绪，请检查模型服务或终端日志") from exc


def _validate(plan, question):
    if not isinstance(plan, dict) or plan.get("intent") not in INTENTS:
        raise HTTPException(422, "未能理解问题，请换一种说法或补全查询条件")
    plan = dict(plan)
    # 统一纠正规划歧义：问“哪一年最多”是在求年份，不是漏填年份。
    # 这里仅确定查询维度，所有面积仍由数据库和图谱计算。
    aliases = {1: ("耕地", "农田"), 2: ("林地", "森林"), 3: ("灌丛",),
               4: ("草地",), 5: ("水体",), 6: ("积雪", "冰川"),
               7: ("裸地", "荒地"), 8: ("建设用地", "建筑用地", "不透水面"), 9: ("湿地",)}
    name_to_code = {name: code for code, names in aliases.items() for name in names}
    names_pattern = "|".join(re.escape(name) for name in sorted(name_to_code, key=len, reverse=True))
    directed_matches = list(re.finditer(
        rf"(?P<source>{names_pattern})\s*(?:转为|转成|转向|转入|→|到|转)\s*"
        rf"(?P<target>{names_pattern})", question))
    directed = directed_matches[0] if directed_matches else None
    flow_pairs = [(name_to_code[m.group("source")], name_to_code[m.group("target")])
                  for m in directed_matches]
    if directed:
        plan["from_class"] = name_to_code[directed.group("source")]
        plan["to_class"] = name_to_code[directed.group("target")]
    seeking_year = any(word in question for word in
                       ("哪一年", "哪两年", "哪段时间", "什么时候", "何时"))
    seeking_extreme = any(word in question for word in
                          ("最多", "最少", "最大", "最小", "最高", "最低"))
    if seeking_year and seeking_extreme:
        if directed:
            plan["intent"] = "extreme_transition_interval"
        elif any(word in question for word in ("哪一年到哪一年", "哪两年", "区间", "逐年变化")):
            plan["intent"] = "extreme_interval"
        else:
            plan["intent"] = "extreme_year"
        plan["extreme"] = "min" if any(w in question for w in ("最少", "最小", "最低")) else "max"
    mentioned = {code for code, names in aliases.items() if any(name in question for name in names)}
    year_tokens = [int(y) for y in re.findall(r"(?<!\d)(?:19\d{2}|20[0-2]\d)(?!\d)", question)]
    four_years = len(year_tokens) == 4
    annual_flow_comparison = (len(year_tokens) == 2 and "还是" in question
                              and any(w in question for w in ("转出", "转入", "转为", "转成")))
    if four_years:
        if len(set(flow_pairs)) > 1:
            raise HTTPException(400, "两个区间含不同转移方向，请逐一写出‘起止年 地类转地类’以便分别核算")
        plan["intent"] = "period_compare"
        plan["classes"] = sorted(mentioned)
        if not directed:
            plan["from_class"] = plan["to_class"] = None
    if annual_flow_comparison and len(flow_pairs) < 2:
        plan["intent"] = "period_compare"
        plan["classes"] = sorted(mentioned)
        if not directed:
            plan["from_class"] = plan["to_class"] = None
    asking_presence = any(w in question for w in ("有没有", "是否有", "存在吗", "有冰川", "有积雪", "有湿地", "有水体", "有林地", "有耕地"))
    if asking_presence and mentioned and not seeking_extreme:
        plan["intent"] = "presence"
        plan["classes"] = sorted(mentioned)
    if (len(flow_pairs) == 2 and len(year_tokens) == 2 and
            any(w in question for w in ("还是", "哪种", "哪个", "更大", "更多", "相差", "比较"))):
        plan["intent"] = "flow_compare"
        plan["classes"] = sorted(mentioned)
    elif (len(year_tokens) == 2 and len(mentioned) == 1
          and any(w in question for w in ("主要转", "转成了哪", "转成哪", "转为什么", "转为哪", "转入哪"))):
        plan["intent"] = "transition_destination_rank"
        plan["classes"] = sorted(mentioned)
        plan["from_class"] = next(iter(mentioned))
        plan["to_class"] = None
    if "斑块" in question and any(w in question for w in ("多少", "数量", "几个", "几块")):
        plan["intent"] = "patch_count"
        plan["classes"] = sorted(mentioned)
        plan["from_class"] = plan["to_class"] = None
    if ("耕地" in question and any(w in question for w in ("城市扩张", "建设用地"))
            and any(w in question for w in ("造成", "导致", "原因", "是否"))):
        plan["intent"] = "causal_assessment"
        plan["classes"] = [1, 8]
        plan["from_class"] = 1
        plan["to_class"] = 8
    intent = plan["intent"]
    codes = plan.get("classes")
    if not isinstance(codes, list) or any(type(c) is not int or c not in CLASS_NAMES for c in codes):
        raise HTTPException(422, "地类解析不确定，请明确写出地类名称")
    codes = list(dict.fromkeys(codes))
    planned = set(codes) | {c for c in (plan.get("from_class"), plan.get("to_class")) if type(c) is int}
    if mentioned and planned != mentioned and plan["intent"] != "causal_assessment":
        raise HTTPException(422, "地类解析与问题不一致，请明确写出需要查询的地类")
    years_in_question = [int(y) for y in re.findall(r"(?<!\d)(?:19\d{2}|20[0-2]\d)(?!\d)", question)]
    if len(years_in_question) == 4:
        plan["intent"] = "period_compare"
        intent = "period_compare"
    elif len(years_in_question) > 2:
        raise HTTPException(400, "请使用两个年份区间（共四个年份），或只写一个起止区间")
    if any(y < 1990 or y > 2025 for y in years_in_question):
        raise HTTPException(400, "年份须在 1990–2025 范围内")
    period_pairs = None
    if plan["intent"] == "period_compare":
        if annual_flow_comparison:
            if any(year >= 2025 for year in years_in_question):
                raise HTTPException(400, "2025 年之后没有年度 CLCD，无法统计 2025→2026 的转出")
            period_pairs = tuple((year, year + 1) for year in years_in_question)
        elif len(years_in_question) == 4:
            period_pairs = ((years_in_question[0], years_in_question[1]),
                            (years_in_question[2], years_in_question[3]))
        else:
            raise HTTPException(400, "请写出两个待比较的年份，或两个完整的起止年份区间")
        if any(a >= b for a, b in period_pairs):
            raise HTTPException(400, "每个比较区间的起始年都要早于结束年")
    else:
        years_in_question = list(dict.fromkeys(years_in_question))
    start, end = plan.get("start_year"), plan.get("end_year")
    if any(type(y) is not int or y < 1990 or y > 2025 for y in (start, end) if y is not None):
        raise HTTPException(422, "年份解析有误，请明确指定 1990–2025 的年份")
    # 明确写出的年份必须原样使用；不能让模型虚构另一个年份。
    if period_pairs:
        start = min(a for a, _ in period_pairs)
        end = max(b for _, b in period_pairs)
    elif len(years_in_question) == 2:
        start, end = years_in_question
    elif len(years_in_question) == 1:
        if intent in {"annual", "class_rank", "presence"}:
            start = end = years_in_question[0]
        elif intent in {"trend", "net_change", "extreme_interval", "extreme_year", "extreme_transition_interval"}:
            raise HTTPException(400, "请写明起止年份；若查询全时段，可写 1990–2025 年")
    elif intent in {"trend", "net_change", "extreme_interval", "extreme_year", "extreme_transition_interval", "presence", "causal_assessment"}:
        start, end = 1990, 2025
    elif intent not in {"document", "clarify"}:
        raise HTTPException(400, "请明确写出查询年份或年份范围")
    if intent == "clarify":
        raise HTTPException(400, str(plan.get("clarification") or "请补充年份、地类和需要比较的指标")[:180])
    if intent == "document":
        start, end = (1990, 2025) if not years_in_question else (
            (years_in_question[0], years_in_question[0]) if len(years_in_question) == 1
            else tuple(years_in_question))
    if start is None or end is None or start > end:
        raise HTTPException(400, "请按从早到晚的顺序指定起止年份")
    if intent in {"compare", "trend", "net_change", "transition", "changed_areas", "extreme_interval", "extreme_transition_interval", "flow_compare", "transition_destination_rank", "patch_count", "causal_assessment"} and start == end:
        raise HTTPException(400, "该查询需要两个不同年份")
    if intent in {"net_change", "extreme_interval", "extreme_year"} and len(codes) != 1:
        raise HTTPException(400, "该问题请指定且只指定一个地类")
    if intent == "presence" and not codes:
        raise HTTPException(400, "请明确要查询是否存在的地类")
    if intent == "class_rank" and start != end:
        raise HTTPException(400, "比较地类大小请指定一个年份")
    pair = (plan.get("from_class"), plan.get("to_class"))
    if any(c is not None and (type(c) is not int or c not in CLASS_NAMES) for c in pair):
        raise HTTPException(422, "转移地类解析有误，请明确转出和转入地类")
    if intent == "extreme_transition_interval" and (None in pair or pair[0] == pair[1]):
        raise HTTPException(400, "逐年地类转移极值查询请明确不同的转出、转入地类")
    metric = None
    if intent == "period_compare":
        if directed:
            metric = "directed"
        elif "净" in question:
            metric = "net"
        elif "转入" in question:
            metric = "incoming"
        elif "转出" in question:
            metric = "outgoing"
        else:
            metric = "changed_total"
        if metric in {"incoming", "outgoing", "net"} and len(mentioned) != 1:
            raise HTTPException(400, "两个区间的转入／转出对比请指定一个地类")
        if metric == "directed" and (None in pair or pair[0] == pair[1]):
            raise HTTPException(400, "请明确不同的转出和转入地类")
        if mentioned and not codes:
            codes = sorted(mentioned)
    extreme = plan.get("extreme") if plan.get("extreme") in {"max", "min"} else "max"
    measure = plan.get("measure") if plan.get("measure") in {"absolute", "increase", "decrease"} else "absolute"
    threshold_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:km²|km2|平方千米)", question, re.I)
    threshold = float(threshold_match.group(1)) if threshold_match else 0.0
    if intent == "patch_count" and (not threshold_match or threshold > 100):
        raise HTTPException(400, "请写出 0–100 km² 的变化斑块面积阈值")
    explain = bool(plan.get("explain")) and intent not in {"presence", "causal_assessment", "patch_count"}
    return intent, start, end, codes, pair, extreme, measure, explain, period_pairs, metric, flow_pairs, threshold


def _extreme(data, extreme, measure):
    """比较相邻年份，减小值时只考虑指定方向。"""
    pairs = [(a, b, b["area_km2"] - a["area_km2"])
             for a, b in zip(data, data[1:])]
    if measure == "increase":
        pairs = [p for p in pairs if p[2] > 0]
    elif measure == "decrease":
        pairs = [p for p in pairs if p[2] < 0]
    if not pairs:
        raise HTTPException(404, "所选时段没有符合该增减方向的相邻年份")
    return (max if extreme == "max" else min)(pairs, key=lambda p: (abs(p[2]), -p[0]["year"]))


def _annual_transition_series(start, end, source, target):
    """图谱中的相邻年份有向转移，未发生转移的年份补零。"""
    password = os.environ.get("NEO4J_PASSWORD")
    if not password:
        raise HTTPException(503, "逐年转移查询需要 Neo4j；请在服务环境设置 NEO4J_PASSWORD")
    try:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(
            os.environ.get("NEO4J_URI", "bolt://127.0.0.1:7687"),
            auth=(os.environ.get("NEO4J_USER", "neo4j"), password),
        )
        try:
            records, _, _ = driver.execute_query(
                """MATCH (a:Observation)-[t:TRANSITIONS_TO]->(b:Observation)
                   WHERE a.class_code = $source AND b.class_code = $target
                     AND a.year >= $start AND b.year <= $end
                     AND b.year = a.year + 1
                   RETURN a.year AS year, sum(t.pixel_count) AS pixels
                   ORDER BY year""",
                source=source, target=target, start=start, end=end,
                database_=os.environ.get("NEO4J_DATABASE", "neo4j"),
            )
        finally:
            driver.close()
    except Exception as exc:
        print(f"逐年转移图谱查询失败：{type(exc).__name__}: {exc}")
        raise HTTPException(503, "Neo4j 逐年转移数据未就绪，请检查图谱和服务端日志") from exc
    if not records:
        raise HTTPException(404, "该时段没有对应地类的逐年转移记录；请核对图谱导入是否完整")
    counts = {int(r["year"]): int(r["pixels"]) for r in records}
    return [{"start_year": year, "end_year": year + 1,
             "pixel_count": counts.get(year, 0),
             "area_km2": round(counts.get(year, 0) * .0009, 4)}
            for year in range(start, end)]


def _fallback_for_two_flows(question):
    """模型规划不可用时，仅识别两个明确方向、一个明确区间的比较。"""
    years = [int(y) for y in re.findall(r"(?<!\d)(?:19\d{2}|20[0-2]\d)(?!\d)", question)]
    aliases = {"耕地": 1, "林地": 2, "灌丛": 3, "草地": 4, "水体": 5,
               "裸地": 7, "建设用地": 8, "湿地": 9}
    names = "|".join(sorted(aliases, key=len, reverse=True))
    pairs = re.findall(rf"({names})\s*(?:转为|转成|转向|→)\s*({names})", question)
    if (len(years) != 2 or len(pairs) != 2 or
            not any(w in question for w in ("哪种", "哪个", "更大", "更多", "还是", "相差", "比较"))):
        return None
    codes = sorted({aliases[name] for pair in pairs for name in pair})
    return {"intent": "flow_compare", "start_year": years[0], "end_year": years[1],
            "classes": codes, "from_class": aliases[pairs[0][0]],
            "to_class": aliases[pairs[0][1]], "extreme": None, "measure": None,
            "explain": False, "clarification": ""}


def answer_question(question):
    """白名单执行器：计划由模型提出，数据计算由已实现的接口完成。"""
    from app import (annual_statistics, compare_statistics, area_timeseries,
                     yearly_net_change, transition_matrix, changed_area_statistics,
                     graph_subgraph, _retrieve_documents, _generate_from_evidence)
    try:
        plan, planner_mode = _model_json(question)
    except HTTPException as exc:
        plan = _fallback_for_two_flows(question) if exc.status_code == 503 else None
        if plan is None:
            raise
        planner_mode = "validated_fallback"
    try:
        parsed = _validate(plan, question)
    except HTTPException as first_error:
        if first_error.status_code not in (400, 422):
            raise
        try:
            plan, planner_mode = _model_json(question, str(first_error.detail))
        except HTTPException as exc:
            plan = _fallback_for_two_flows(question) if exc.status_code == 503 else None
            if plan is None:
                raise
            planner_mode = "validated_fallback"
        parsed = _validate(plan, question)
    intent, start, end, codes, pair, extreme, measure, explain, period_pairs, metric, flow_pairs, threshold = parsed
    selected = codes or list(CLASS_NAMES)
    mode = intent if intent in {"compare", "transition", "changed_areas"} else "timeseries"
    facts = []
    if intent == "annual":
        mode = "annual"
        facts = [r for r in annual_statistics(start)["classes"] if r["class_code"] in selected]
        details = "；".join(f'{r["class_name"]} {r["area_km2"]:.4f} km²（{r["percentage"]:.2f}%）' for r in facts)
        answer = f"官渡区 {start} 年：{details}。"
    elif intent == "class_rank":
        mode = "annual"
        rows = [r for r in annual_statistics(start)["classes"] if r["class_code"] in selected]
        row = (max if extreme == "max" else min)(rows, key=lambda r: r["area_km2"])
        facts = [row]
        answer = f"官渡区 {start} 年，{'面积最大' if extreme == 'max' else '面积最小'}的地类是{row['class_name']}：{row['area_km2']:.4f} km²。"
    elif intent == "compare":
        facts = [r for r in compare_statistics(start, end)["changes"] if r["class_code"] in selected]
        answer = f"官渡区 {start} 与 {end} 年相比：" + "；".join(
            f'{r["class_name"]}面积净变化 {r["net_change_km2"]:+.4f} km²' for r in facts) + "。"
    elif intent == "period_compare":
        mode = "periodcompare"
        code = next(iter(codes), None)
        def count_pixels(result):
            flows = result["flows"]
            if metric == "outgoing":
                return sum(r["pixel_count"] for r in flows if r["from_code"] == code)
            if metric == "incoming":
                return sum(r["pixel_count"] for r in flows if r["to_code"] == code)
            if metric == "net":
                return (sum(r["pixel_count"] for r in flows if r["to_code"] == code)
                        - sum(r["pixel_count"] for r in flows if r["from_code"] == code))
            if metric == "directed":
                return next(r["pixel_count"] for r in result["matrix"]
                            if r["from_code"] == pair[0] and r["to_code"] == pair[1])
            return result["changed_pixel_count"]
        facts = []
        for first, last in period_pairs:
            pixels = count_pixels(transition_matrix(first, last))
            facts.append({"start_year": first, "end_year": last,
                          "pixel_count": pixels, "area_km2": round(pixels * .0009, 4)})
        if metric == "directed":
            measure_label = f"{CLASS_NAMES[pair[0]]}→{CLASS_NAMES[pair[1]]}"
        elif metric == "changed_total":
            measure_label = "全区地类转移"
        else:
            measure_label = f"{CLASS_NAMES[code]}{'转出' if metric == 'outgoing' else '转入' if metric == 'incoming' else '净转入'}"
        a, b = facts
        if a["pixel_count"] == b["pixel_count"]:
            conclusion = "两个区间相同"
        else:
            winner = a if a["pixel_count"] > b["pixel_count"] else b
            conclusion = f"{winner['start_year']}→{winner['end_year']} 年更多"
        answer = (f"结论：官渡区{measure_label}，{conclusion}。\n"
                  f"依据：{a['start_year']}→{a['end_year']} 年 {a['area_km2']:.4f} km²；"
                  f"{b['start_year']}→{b['end_year']} 年 {b['area_km2']:.4f} km²；"
                  f"相差 {abs(a['pixel_count']-b['pixel_count'])*.0009:.4f} km²。"
                  "分别按各区间起止年的转移矩阵计算。")
    elif intent == "flow_compare":
        mode = "periodcompare"
        matrix = transition_matrix(start, end)["matrix"]
        facts = []
        for source, target in flow_pairs:
            row = next(r for r in matrix if r["from_code"] == source and r["to_code"] == target)
            facts.append({"label": f"{CLASS_NAMES[source]}→{CLASS_NAMES[target]}",
                          "start_year": start, "end_year": end,
                          "pixel_count": row["pixel_count"], "area_km2": row["area_km2"]})
        a, b = facts
        winner = "两种转移面积相同" if a["pixel_count"] == b["pixel_count"] else (
            f"{a['label']}更多" if a["pixel_count"] > b["pixel_count"] else f"{b['label']}更多")
        answer = (f"结论：官渡区 {start}→{end} 年，{winner}。\n依据："
                  f"{a['label']} {a['area_km2']:.4f} km²，{b['label']} {b['area_km2']:.4f} km²；"
                  f"相差 {abs(a['pixel_count']-b['pixel_count'])*.0009:.4f} km²。"
                  "按同一起止年份转移矩阵中的两个非对角格计算。")
    elif intent == "transition_destination_rank":
        mode = "transition"
        source = codes[0]
        flows = [r for r in transition_matrix(start, end)["flows"] if r["from_code"] == source]
        if flows:
            row = max(flows, key=lambda r: r["pixel_count"])
            facts = [row]
            answer = (f"官渡区 {start}→{end} 年，{CLASS_NAMES[source]}转出最多的是"
                      f"{row['to_name']}，面积 {row['area_km2']:.4f} km²。")
        else:
            answer = f"官渡区 {start}→{end} 年，转移矩阵未记录{CLASS_NAMES[source]}转为其他地类。"
    elif intent == "patch_count":
        mode = "changedareas"
        # “超过”是严格大于；阈值若恰好等于完整像元面积，排除等于的斑块。
        minimum = threshold + 1e-8 if "超过" in question else threshold
        result = changed_area_statistics(start, end, minimum)
        if result["patch_count"] is None:
            raise HTTPException(400, "斑块计数需要大于一个像元面积（0.0009 km²）的阈值")
        facts = [{"patch_count": result["patch_count"],
                  "changed_pixel_count": result["changed_pixel_count"],
                  "changed_area_km2": result["changed_area_km2"]}]
        answer = (f"官渡区 {start}→{end} 年，面积{'超过' if '超过' in question else '不小于'}"
                  f" {threshold:g} km² 的连续变化斑块有 {result['patch_count']} 个。")
    elif intent == "causal_assessment":
        mode = "transition"
        changes = compare_statistics(start, end)["changes"]
        farmland = next(r for r in changes if r["class_code"] == 1)
        built = next(r for r in changes if r["class_code"] == 8)
        row = next(r for r in transition_matrix(start, end)["matrix"]
                   if r["from_code"] == 1 and r["to_code"] == 8)
        facts = [farmland, built, row]
        answer = ("不能仅凭 CLCD 断定耕地减少由城市扩张造成；可确认有耕地转为建设用地的地表覆盖转换。"
                  f"官渡区 {start}→{end} 年，耕地→建设用地 {row['area_km2']:.4f} km²，"
                  f"耕地净变化 {farmland['net_change_km2']:+.4f} km²，"
                  f"建设用地净变化 {built['net_change_km2']:+.4f} km²。")
    elif intent in {"trend", "extreme_interval", "extreme_year"}:
        result = area_timeseries(start, end, ",".join(map(str, selected)))
        facts = result["series"]
        if intent == "extreme_interval":
            series = facts[0]
            a, b, delta = _extreme(series["data"], extreme, measure)
            facts = [{"class_code": series["class_code"], "class_name": series["class_name"],
                      "start_year": a["year"], "end_year": b["year"],
                      "net_change_km2": round(delta, 4)}]
            answer = (f"在官渡区 {start}–{end} 年逐年比较中，{series['class_name']}"
                      f"{'增幅' if measure == 'increase' else '减幅' if measure == 'decrease' else '面积变化幅度'}"
                      f"{'最大' if extreme == 'max' else '最小'}的相邻年份是 {a['year']}–{b['year']} 年："
                      f"从 {a['area_km2']:.4f} km² 变为 {b['area_km2']:.4f} km²，"
                      f"{'增加' if delta >= 0 else '减少'} {abs(delta):.4f} km²。"
                      "变化幅度按相邻两年面积差的绝对值计算。")
        elif intent == "extreme_year":
            series = facts[0]
            row = (max if extreme == "max" else min)(series["data"], key=lambda r: r["area_km2"])
            facts = [row]
            answer = f"官渡区 {start}–{end} 年，{series['class_name']}面积{'最大' if extreme == 'max' else '最小'}的是 {row['year']} 年：{row['area_km2']:.4f} km²。"
        else:
            answer = f"官渡区 {start}–{end} 年逐年面积：" + "；".join(
                f'{s["class_name"]}由 {s["data"][0]["area_km2"]:.4f} 变为 {s["data"][-1]["area_km2"]:.4f} km²' for s in facts) + "。逐年值见图表。"
    elif intent == "presence":
        mode = "annual" if start == end else "timeseries"
        result = area_timeseries(start, end, ",".join(map(str, codes)))
        facts = result["series"]
        descriptions = []
        for series in facts:
            found = [r for r in series["data"] if r["area_km2"] > 0]
            label = f"官渡区 {start} 年" if start == end else f"官渡区 {start}–{end} 年逐年 CLCD"
            if series["class_code"] == 6:
                if found:
                    descriptions.append(f"{label}中有 {len(found)} 个年份记录到‘积雪／冰川’合并类别；仅凭 CLCD 无法判定它是冰川还是积雪，不能据此确认官渡区有冰川。")
                else:
                    descriptions.append(f"{label}中未记录到‘积雪／冰川’类别；这不等于证明现实中绝对没有冰川，若要确认需冰川专题数据或实地资料。")
            elif found:
                descriptions.append(f"{label}中记录到{series['class_name']}，涉及 {len(found)} 个年份；该结论只表示在 CLCD 的对应类别中出现。")
            else:
                descriptions.append(f"{label}中未记录到{series['class_name']}类别；请注意数据分辨率和分类误差。")
        answer = "".join(descriptions)
    elif intent == "net_change":
        mode = "netchange"
        result = yearly_net_change(start, end, codes[0])
        facts = result["data"]
        overall = sum(r["net_change_km2"] for r in facts)
        answer = f"官渡区{result['class_name']}从 {start} 至 {end} 年累计净变化 {overall:+.4f} km²；每年的变化值见下方图表。"
    elif intent == "extreme_transition_interval":
        a, b = pair
        rows = _annual_transition_series(start, end, a, b)
        winner = (max if extreme == "max" else min)(
            rows, key=lambda r: (r["pixel_count"], -r["start_year"]))
        facts = [winner]
        # 地图和转移矩阵聚焦最显著的相邻年份；结果仍标注原始搜索范围。
        search_start, search_end = start, end
        start, end = winner["start_year"], winner["end_year"]
        selected = list(dict.fromkeys((a, b)))
        mode = "transition"
        answer = (f"在官渡区 {search_start}–{search_end} 年全部相邻年份中，"
                  f"{CLASS_NAMES[a]}转为{CLASS_NAMES[b]}面积{'最多' if extreme == 'max' else '最少'}的是"
                  f" {start}→{end} 年：{winner['area_km2']:.4f} km²"
                  f"（{winner['pixel_count']} 个像元）。这里的“{start} 年”指转出年份。")
    elif intent == "transition":
        result = transition_matrix(start, end)
        a, b = pair
        if a is not None and b is not None:
            facts = [r for r in result["matrix"] if r["from_code"] == a and r["to_code"] == b]
            row = facts[0]
            answer = f"官渡区 {start} 年{CLASS_NAMES[a]}到 {end} 年{CLASS_NAMES[b]}的起止年转移面积为 {row['area_km2']:.4f} km²。"
        else:
            facts = sorted([r for r in result["flows"] if r["from_code"] in selected or r["to_code"] in selected],
                           key=lambda r: r["pixel_count"], reverse=True)[:8]
            answer = f"官渡区 {start}–{end} 年总转移面积 {result['changed_area_km2']:.4f} km²。主要转移：" + "；".join(
                f'{r["from_name"]}→{r["to_name"]} {r["area_km2"]:.4f} km²' for r in facts) + "。"
    elif intent == "changed_areas":
        result = changed_area_statistics(start, end, 0.0)
        facts = [r for r in result["classes"] if r["class_code"] in selected]
        answer = f"官渡区 {start}–{end} 年变化区域面积 {result['changed_area_km2']:.4f} km²。" + "；".join(
            f'{r["class_name"]}转入 {r["incoming_area_km2"]:.4f} km²、转出 {r["outgoing_area_km2"]:.4f} km²' for r in facts) + "。"
    else:
        mode = "annual" if start == end else "timeseries"
        answer = "当前文献可提供背景说明；仅凭土地覆盖栅格不能证明变化的具体原因。"

    # 统一先给直接回答，再展示数据依据和统计口径。
    if intent not in {"period_compare", "flow_compare"}:
        method = {
            "annual": "对应年份 CLCD 年度面积表；占比以该年全区面积为分母。",
            "class_rank": "比较该年各地类的 CLCD 面积。",
            "compare": "比较两个年份的 CLCD 地类面积，净变化为结束年减起始年。",
            "trend": "读取区间内逐年 CLCD 面积；完整序列见下方图表。",
            "extreme_interval": "逐一计算相邻年份的面积差，再按问题指定的增减方向或变化幅度比较。",
            "extreme_year": "比较区间内各年份该类地类的 CLCD 面积。",
            "presence": "核对所选年份的 CLCD 分类；分类记录不能替代独立的实地证据。",
            "net_change": "每年的净变化是本年面积减去上一年面积，不能当作转出总量。",
            "extreme_transition_interval": "比较区间内每对相邻年份图谱中的指定地类转移像元数。",
            "transition": "起止年 CLCD 像元叠置得到的地类转移矩阵。",
            "changed_areas": "统计两个年份中分类编码不同的像元；当前斑块面积阈值为零。",
            "document": "根据已入库资料作背景说明；没有证据时不推断原因。",
            "transition_destination_rank": "比较耕地等指定来源类别转向所有其他地类的非对角转移矩阵格，不计未变化像元。",
            "patch_count": "按 30 米原始网格的八邻接连续变化像元形成斑块，筛选面积后计数；筛出的变化总面积不等于斑块数。",
            "causal_assessment": "起止年 CLCD 转移矩阵与年度面积净变化只说明覆盖变化；认定城市扩张的驱动原因仍需独立的规划、人口和用地证据。",
        }[intent]
        answer = f"结论：{answer}\n依据：{method}"

    graph = None
    if os.environ.get("NEO4J_PASSWORD"):
        try:
            graph = graph_subgraph(start, end, ",".join(map(str, selected)))
        except HTTPException:
            pass
    documents, retrieval_status = ([], "not_needed")
    paths, path_status = ([], "not_needed")
    generation_mode = f"planner_{planner_mode}"
    if explain or intent == "document":
        from graphrag_evidence import graph_paths
        if intent not in {"transition", "period_compare", "flow_compare"}:
            paths, path_status = graph_paths(start, end, selected)
        else:
            path_status = "起止年转移以直接叠置矩阵为准；不引用相邻年图谱路径"
        documents, retrieval_status = _retrieve_documents(question)
        # 图谱汇总相邻转换，不能作为起止年矩阵的证据。
        evidence_graph = ({"links": paths} if paths else None)
        answer, generation_mode = _generate_from_evidence(question, answer, evidence_graph, documents)
        if not paths and not documents:
            answer += "\n补充说明：当前未检索到可用的图谱路径或文档片段，无法给出有依据的进一步解释。"
    return {
        "answer": answer,
        "query": {"start_year": start, "end_year": end,
                  "class_codes": selected, "chart_mode": mode,
                  "comparison_periods": facts if intent in {"period_compare", "flow_compare"} else None,
                  "comparison_label": measure_label if intent == "period_compare" else "地类转移方向" if intent == "flow_compare" else None,
                  "min_patch_km2": minimum if intent == "patch_count" else 0},
        "facts": facts, "graph": graph,
        "evidence": {"statistics": "官渡区 CLCD 年度栅格及本地统计结果",
                     "documents": documents, "retrieval_status": retrieval_status,
                     "graph_paths": paths, "graph_status": path_status,
                     "graph_semantics": "两跳为类别关系链，不代表同一像元连续轨迹；仅表示覆盖变化，不证明驱动原因"},
        "generation_mode": generation_mode,
        "note": "模型仅解析问题；统计面积从本地数据计算。年份/地类不明时要求补充条件。",
    }
