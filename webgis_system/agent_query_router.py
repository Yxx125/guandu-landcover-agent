"""将 LangGraph 工具查询与现有广覆盖查询规划器统一到一个网页入口。

本模块不会把规划器的结果冒充成 Function Calling；返回字段中记录实际路径。
"""

import re

from fastapi import HTTPException


NAMES = {1: '耕地', 2: '林地', 3: '灌丛', 4: '草地', 5: '水体',
         6: '积雪／冰川', 7: '裸地', 8: '建设用地（不透水面）', 9: '湿地'}
ALIASES = {'耕地': 1, '林地': 2, '灌丛': 3, '草地': 4, '水体': 5,
           '积雪': 6, '冰川': 6, '裸地': 7, '建设用地': 8, '不透水面': 8, '湿地': 9}
YEAR = re.compile(r'(?<!\d)(?:19\d{2}|20[0-2]\d)(?!\d)')


def _codes(text):
    return {code for name, code in ALIASES.items() if name in text}


def _annual(year, code):
    from app import annual_statistics
    return next(row for row in annual_statistics(year)['classes'] if row['class_code'] == code)


def _matrix(start, end):
    from app import transition_matrix
    rows = transition_matrix(start, end)['matrix']
    return {(row['from_code'], row['to_code']): row for row in rows}


def _result(answer, facts, start, end, codes, chart='compare', source='官渡区 CLCD 年度面积表'):
    # app.agent_full_ask 将 multi_tool 视为已具备 query/evidence 的完整响应。
    return {'answer': '结论：' + answer, 'facts': facts, 'tool': 'multi_tool',
            'generation_mode': 'validated_extended_metrics',
            'tool_trace': [{'step': 'run_tool', 'tool': 'extended_metrics', 'source': source}],
            'query': {'start_year': start, 'end_year': end, 'class_codes': codes,
                      'chart_mode': chart, 'min_patch_km2': 0},
            'evidence': {'statistics': source, 'documents': [], 'graph_paths': [],
                         'retrieval_status': 'not_needed'}}


def answer_extended(question):
    """匹配明确的指标；不明确的查询交给既有规划器。匹配后不吞掉数据故障。"""
    q = question.strip()
    years = [int(x.group()) for x in YEAR.finditer(q)]
    codes = _codes(q)
    # Explicit mixed request: one annual area plus one interval net-change.
    # Handle this deterministically before the model planner so the two tools
    # receive their own unambiguous year arguments.
    interval = re.search(
        r"((?:19\d{2}|20[0-2]\d))\s*(?:年)?\s*(?:到|至|→|—|–|-)\s*"
        r"((?:19\d{2}|20[0-2]\d))\s*年?", q
    )
    annual = re.search(r"((?:19\d{2}|20[0-2]\d))\s*年[^。；，,]*?面积", q)
    if interval and annual and len(codes) == 1:
        start, end = map(int, interval.groups())
        year = int(annual.group(1))
        if not (1990 <= start < end <= 2025 and 1990 <= year <= 2025):
            raise HTTPException(400, "每个查询年份须在 1990–2025 年内，且区间起始年早于结束年")
        code = next(iter(codes))
        first = _annual(start, code)
        last = _annual(end, code)
        point = _annual(year, code)
        delta = round(last['area_km2'] - first['area_km2'], 4)
        result = _result(
            f"{year}年{NAMES[code]}面积为 {point['area_km2']:.4f} km²；"
            f"{start}→{end} 年净变化为 {delta:+.4f} km²。",
            {'annual': point, 'compare': {'start': first, 'end': last,
             'net_change_km2': delta}}, start, end, [code], 'compare',
            '官渡区 CLCD 年度面积表'
        )
        result['generation_mode'] = 'agent_multi_policy'
        result['tool_trace'] = [
            {'step': 'run_tool', 'tool': 'get_annual_area', 'year': year},
            {'step': 'run_tool', 'tool': 'compare_years', 'start_year': start, 'end_year': end},
        ]
        return result
    if years and (min(years) < 1990 or max(years) > 2025):
        raise HTTPException(400, '年份应在 1990–2025 年')
    if any(x in q for x in ('同一像元', '同一位置', '先转', '再转', '然后转')) and len(years) >= 3:
        raise HTTPException(400, '多年同一像元轨迹需要逐像元叠置，现有汇总转移矩阵无法证明该路径')
    if len(years) == 2 and years[0] >= years[1] and any(w in q for w in ('增长率', '变化率', '保持率', '转化比例', '转化率', '转出', '转入', '稳定', '前五')):
        raise HTTPException(400, '起始年份必须早于结束年份')
    if len(years) == 2 and years[0] < years[1]:
        start, end = years
        if len(codes) == 1 and any(w in q for w in ('增长率', '变化率', '减少率', '下降率', '提高了几个百分点', '占比变化', '占比提高', '占比下降')):
            code = next(iter(codes))
            a, b = _annual(start, code), _annual(end, code)
            delta = b['area_km2'] - a['area_km2']
            facts = {'start': a, 'end': b, 'net_change_km2': round(delta, 4),
                     'share_change_percentage_points': round(b['percentage'] - a['percentage'], 4)}
            if any(w in q for w in ('百分点', '占比')):
                value = facts['share_change_percentage_points']
                answer = f'{start}→{end} 年{NAMES[code]}占全区比例变化 {value:+.2f} 个百分点。\n依据：{start} 年 {a["percentage"]:.2f}%，{end} 年 {b["percentage"]:.2f}%；结束年占比减起始年占比。'
            else:
                if a['area_km2'] == 0:
                    answer = f'{start} 年{NAMES[code]}面积为零，无法以该年为分母计算面积变化率。\n依据：{end} 年面积 {b["area_km2"]:.4f} km²。'
                    facts['rate_percent'] = None
                else:
                    rate = delta / a['area_km2'] * 100
                    facts['rate_percent'] = round(rate, 4)
                    answer = f'{start}→{end} 年{NAMES[code]}面积变化率 {rate:+.2f}%。\n依据：({b["area_km2"]:.4f}−{a["area_km2"]:.4f})÷{a["area_km2"]:.4f}×100%；面积净变化 {delta:+.4f} km²。'
            return _result(answer, facts, start, end, [code])
        conversion = re.search(r'(耕地|林地|灌丛|草地|水体|积雪|冰川|裸地|建设用地|不透水面|湿地)(?:\s*到\s*(?:19\d{2}|20[0-2]\d)\s*年?)?\s*(?:转为|转成|变成|转向|→)\s*(耕地|林地|灌丛|草地|水体|积雪|冰川|裸地|建设用地|不透水面|湿地)', q)
        if conversion and any(w in q for w in ('百分之', '百分比', '比例', '占原', '转化率')):
            source, target = (ALIASES[x] for x in conversion.groups())
            if source == target:
                raise HTTPException(400, '转换比例请指定两个不同的地类；保持率可单独查询')
            matrix = _matrix(start, end)
            base = sum(matrix[source, j]['pixel_count'] for j in NAMES)
            moved = matrix[source, target]['pixel_count']
            pct = moved / base * 100 if base else None
            answer = (f'{start} 年{NAMES[source]}中，到 {end} 年转为{NAMES[target]}的比例为 {pct:.2f}%。\n依据：{moved} 个转换像元÷{base} 个起始类像元×100%；转换面积 {moved*.0009:.4f} km²。' if pct is not None else f'{start} 年{NAMES[source]}无有效像元，转换比例不可定义。')
            return _result(answer, {'from_code': source, 'to_code': target, 'pixels': moved, 'base_pixels': base, 'rate_percent': round(pct, 4) if pct is not None else None}, start, end, [source, target], 'transition', '官渡区 CLCD 起止年像元转移矩阵')
        if any(w in q for w in ('保持不变率', '保持率', '稳定率', '最稳定', '保持原类')):
            matrix = _matrix(start, end)
            selected = sorted(codes or NAMES)
            facts = []
            for code in selected:
                base = sum(matrix[code, j]['pixel_count'] for j in NAMES)
                stay = matrix[code, code]['pixel_count']
                facts.append({'class_code': code, 'class_name': NAMES[code], 'base_pixels': base,
                              'unchanged_pixels': stay, 'rate_percent': round(stay/base*100, 4) if base else None})
            valid = [x for x in facts if x['rate_percent'] is not None]
            if not valid:
                answer = '所选地类在起始年均无有效像元，保持率不可定义。'
            elif '最稳定' in q:
                winner = max(valid, key=lambda x: (x['rate_percent'], -x['class_code']))
                answer = f'{start}→{end} 年{winner["class_name"]}保持率最高，为 {winner["rate_percent"]:.2f}%。\n依据：同类像元÷起始年该地类像元；' + '；'.join(f'{x["class_name"]} {x["rate_percent"]:.2f}%' for x in valid) + '。'
            else:
                answer = f'{start}→{end} 年保持原类的比例：' + '；'.join(f'{x["class_name"]} {x["rate_percent"]:.2f}%' if x['rate_percent'] is not None else f'{x["class_name"]}无起始像元' for x in facts) + '。\n依据：各地类矩阵对角像元数÷该行起始像元总数。'
            return _result(answer, facts, start, end, selected, 'transition', '官渡区 CLCD 起止年像元转移矩阵')
        if len(codes) == 1 and any(w in q for w in ('转入转出', '转入、转出', '转入和转出', '转入多少、转出多少', '分别转入', '分别转出', '净变化拆解')):
            code = next(iter(codes))
            matrix = _matrix(start, end)
            incoming = sum(matrix[i, code]['pixel_count'] for i in NAMES if i != code)
            outgoing = sum(matrix[code, j]['pixel_count'] for j in NAMES if j != code)
            net = incoming - outgoing
            answer = f'{start}→{end} 年{NAMES[code]}转入 {incoming*.0009:.4f} km²，转出 {outgoing*.0009:.4f} km²，净变化 {net*.0009:+.4f} km²。\n依据：矩阵非对角列和减非对角行和；净变化＝转入−转出。'
            return _result(answer, {'class_code': code, 'incoming_pixels': incoming, 'outgoing_pixels': outgoing, 'net_pixels': net, 'incoming_km2': round(incoming*.0009, 4), 'outgoing_km2': round(outgoing*.0009, 4), 'net_km2': round(net*.0009, 4)}, start, end, [code], 'transition', '官渡区 CLCD 起止年像元转移矩阵')
        rank = re.search(r'前\s*([一二三四五六七八九十\d]+)\s*(?:种|个|类|名)?', q)
        if rank and any(w in q for w in ('转移', '转化', '转换', '流向')):
            token = rank.group(1)
            chinese = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
            k = int(token) if token.isdigit() else chinese.get(token)
            if k is None or not 1 <= k <= 20:
                raise HTTPException(400, '转移排名仅支持前 1–20 种')
            matrix = _matrix(start, end)
            flows = [r for (i, j), r in matrix.items() if i != j and r['pixel_count'] > 0 and (not codes or i in codes or j in codes)]
            flows.sort(key=lambda r: (-r['pixel_count'], r['from_code'], r['to_code']))
            facts = flows[:k]
            answer = (f'{start}→{end} 年转移面积前 {min(k, len(facts))} 种为：' + '；'.join(f'{n}. {NAMES[x["from_code"]]}→{NAMES[x["to_code"]]} {x["area_km2"]:.4f} km²' for n, x in enumerate(facts, 1)) + '。\n依据：起止年矩阵非对角像元数降序；同面积按类别编码排序。') if facts else '指定范围内没有发生地类转换。'
            return _result(answer, facts, start, end, sorted(codes or NAMES), 'transition', '官渡区 CLCD 起止年像元转移矩阵')
    if len(years) in (0, 2) and len(codes) == 1 and any(w in q for w in ('面积最大', '面积最多', '面积最小', '面积最少')) and any(w in q for w in ('前三', '前五', '前3', '前5', '哪三年', '哪五年')) and not any(w in q for w in ('转为', '转成', '→')):
        start, end = years if years else (1990, 2025)
        if start >= end:
            raise HTTPException(400, '年度面积排名需要至少两个从早到晚的年份')
        code = next(iter(codes))
        from app import area_timeseries
        data = area_timeseries(start, end, str(code))['series'][0]['data']
        minimum = any(w in q for w in ('最小', '最少'))
        ranking = sorted(data, key=lambda x: ((x['area_km2'] if minimum else -x['area_km2']), x['year']))
        k = 5 if any(w in q for w in ('前五', '前5', '哪五年')) else 3
        facts = ranking[:k]
        answer = f'{start}–{end} 年{NAMES[code]}面积' + ('最小' if minimum else '最大') + f'的前 {len(facts)} 个年份：' + '；'.join(f'{x["year"]} 年 {x["area_km2"]:.4f} km²' for x in facts) + '。\n依据：逐年年度面积表排序；同面积按年份升序。'
        return _result(answer, facts, start, end, [code], 'timeseries', '官渡区 CLCD 逐年面积表')
    direction = re.search(r'(耕地|林地|灌丛|草地|水体|裸地|建设用地|不透水面|湿地)\s*(?:转为|转成|→)\s*(耕地|林地|灌丛|草地|水体|裸地|建设用地|不透水面|湿地)', q)
    if direction and len(years) in (0, 2) and any(w in q for w in ('相邻年份', '逐年转移', '逐年转换')) and any(w in q for w in ('趋势', '最明显', '最多', '排名', '前')):
        start, end = years if years else (1990, 2025)
        if start >= end:
            raise HTTPException(400, '逐年转移至少需要两个年份')
        source, target = (ALIASES[name] for name in direction.groups())
        if source == target:
            raise HTTPException(400, '逐年转移请指定不同的起始和目标地类')
        facts = []
        for year in range(start, end):
            row = _matrix(year, year+1)[source, target]
            facts.append({'start_year': year, 'end_year': year+1, 'pixel_count': row['pixel_count'], 'area_km2': row['area_km2']})
        peak = max(facts, key=lambda x: (x['pixel_count'], -x['start_year']))
        answer = f'{start}–{end} 年相邻年份{NAMES[source]}→{NAMES[target]}转移面积最大的是 {peak["start_year"]}→{peak["end_year"]} 年，{peak["area_km2"]:.4f} km²。\n依据：逐对读取相邻年直接转移矩阵；不把这些流量解释为 {start}→{end} 年的同一批像元轨迹。逐年数据见 facts。'
        return _result(answer, facts, start, end, [source, target], 'timeseries', '官渡区 CLCD 相邻年份直接转移矩阵')
    if len(years) in (0, 2) and len(codes) == 1 and any(w in q for w in ('增长最快', '增幅最大', '下降最快', '降幅最大', '前几名', '前三名', '前五名', '前3名', '前5名')) and not any(w in q for w in ('转为', '转成', '→')):
        start, end = years if years else (1990, 2025)
        if start >= end:
            raise HTTPException(400, '逐年比较至少需要两个从早到晚的年份')
        code = next(iter(codes))
        from app import area_timeseries
        series = area_timeseries(start, end, str(code))['series'][0]['data']
        changes = [{'start_year': a['year'], 'end_year': b['year'], 'delta_km2': round(b['area_km2']-a['area_km2'], 4)} for a, b in zip(series, series[1:])]
        descending = not any(w in q for w in ('下降', '降幅'))
        changes.sort(key=lambda x: ((-x['delta_km2'] if descending else x['delta_km2']), x['end_year']))
        k = 5 if any(w in q for w in ('前五', '前5')) else 3 if any(w in q for w in ('前三', '前3', '前几')) else 1
        facts = changes[:k]
        answer = (f'{start}→{end} 年{NAMES[code]}逐年面积' + ('增加' if descending else '减少') + f'排名前 {len(facts)}：' + '；'.join(f'{x["start_year"]}→{x["end_year"]} 年 {x["delta_km2"]:+.4f} km²' for x in facts) + '。\n依据：相邻年面积相减；负值仍表示减少，不等于地类转入转出面积。')
        return _result(answer, facts, start, end, [code], 'netchange', '官渡区 CLCD 逐年面积表')
    return None


def _compare_explicit_net_periods(question: str):
    """比较任意 2–12 个写明起止年的地类面积净变化。"""
    # “增长/增加/减少”是面积净变化的常见自然语言问法，必须和
    # “净变化”一样优先走确定性的多区间面积计算，不能只取第一个区间。
    if not any(word in question for word in ("净变化", "净增", "净减", "增长", "增加", "减少")):
        return None
    years = [int(x.group()) for x in YEAR.finditer(question)]
    if len(years) < 4:
        return None
    # 必须逐个配对，不能凭年份在句子中的位置猜测区间。
    periods = [(int(a), int(b)) for a, b in re.findall(
        r"(?<!\d)((?:19\d{2}|20[0-2]\d))\s*(?:年)?\s*(?:到|至|→|—|–|-)\s*"
        r"((?:19\d{2}|20[0-2]\d))\s*(?:年)?", question)]
    if len(periods) < 2 or len(periods) > 12 or len(periods) * 2 != len(years):
        return None
    if any(not (1990 <= start < end <= 2025) for start, end in periods):
        raise HTTPException(400, "每个比较区间都须在 1990–2025 年内，且起始年早于结束年")
    codes = _codes(question)
    if len(codes) != 1:
        raise HTTPException(400, "多个区间的面积净变化比较请明确且只指定一个地类")
    if len(set(periods)) != len(periods):
        raise HTTPException(400, "比较区间不能重复")
    code = next(iter(codes))
    # 相同年份只取一次，所有数值来自年度面积表。
    areas = {year: _annual(year, code)["area_km2"]
             for year in {year for pair in periods for year in pair}}
    facts = [{"start_year": start, "end_year": end, "class_code": code,
              "label": NAMES[code], "start_area_km2": areas[start],
              "end_area_km2": areas[end],
              "area_km2": round(areas[end] - areas[start], 4)}
             for start, end in periods]
    # “净变化最大”默认比较变化幅度；明确“净增加最多”才比较有符号增量。
    absolute = not any(word in question for word in ("净增加最大", "净增加最多", "净增最多", "有符号最大"))
    score = lambda row: abs(row["area_km2"]) if absolute else row["area_km2"]
    highest = max(score(row) for row in facts)
    winners = [row for row in facts if score(row) == highest]
    label = "、".join(f'{row["start_year"]}→{row["end_year"]} 年' for row in winners)
    criterion = "净变化幅度（绝对值）" if absolute else "有符号的面积净变化值"
    answer = (f"结论：{label}的{NAMES[code]}{criterion}最大，"
              f"为 {highest:.4f} km²。\n依据：" + "；".join(
                  f'{row["start_year"]}→{row["end_year"]} 年 '
                  f'{row["end_area_km2"]:.4f}−{row["start_area_km2"]:.4f}'
                  f'＝{row["area_km2"]:+.4f} km²' for row in facts) +
              "。各项均为该区间结束年面积减起始年面积；正值为净增加，负值为净减少。")
    return {
        "answer": answer, "facts": facts, "tool": "multi_tool",
        "generation_mode": "validated_multi_period_net_area",
        "tool_trace": [{"step": "run_tool", "tool": "annual_statistics", "year": year}
                       for year in sorted(areas)],
        "query": {"start_year": min(years), "end_year": max(years),
                  "class_codes": [code], "chart_mode": "periodcompare",
                  "comparison_kind": "annual_area", "comparison_label": f"{NAMES[code]}各区间面积净变化",
                  "comparison_periods": facts, "min_patch_km2": 0},
        "evidence": {"statistics": "各区间起止年的官渡区 CLCD 年度面积表",
                     "documents": [], "graph_paths": [], "retrieval_status": "not_needed"},
    }


def _compare_explicit_outgoing_periods(question: str):
    """以 CLCD 非对角行和／列和回答单个或多个区间的转出／转入。"""
    incoming = any(word in question for word in ("转入", "流入"))
    outgoing = any(word in question for word in ("转出", "流出"))
    if not (incoming or outgoing):
        return None
    if incoming and outgoing:
        return None  # 同问两种指标交给组合规划器，不能擅自只算一边。
    direction = "转入" if incoming else "转出"
    if any(word in question for word in ("转为", "转成")) or re.search(
            r"(?:耕地|林地|草地|建设用地|水体|湿地)\s*→\s*"
            r"(?:耕地|林地|草地|建设用地|水体|湿地)", question):
        return None  # 有向 A→B 与地类总转出不是同一指标。
    years = [int(x.group()) for x in YEAR.finditer(question)]
    periods = [(int(a), int(b)) for a, b in re.findall(
        r"(?<!\d)((?:19\d{2}|20[0-2]\d))\s*(?:年)?\s*(?:到|至|→|—|–|-)\s*"
        r"((?:19\d{2}|20[0-2]\d))\s*(?:年)?", question)]
    if not periods or len(periods) > 12 or len(periods) * 2 != len(years):
        return None
    if any(not (1990 <= start < end <= 2025) for start, end in periods):
        raise HTTPException(400, f"{direction}查询的每个区间须在 1990–2025 年内，且起始年早于结束年")
    codes = _codes(question)
    if len(codes) != 1:
        raise HTTPException(400, f"地类{direction}查询请明确且只指定一个地类")
    if len(set(periods)) != len(periods):
        raise HTTPException(400, "比较区间不能重复")
    code = next(iter(codes))
    facts = []
    for start, end in periods:
        matrix = _matrix(start, end)
        pixels = (sum(matrix[source, code]["pixel_count"] for source in NAMES if source != code)
                  if incoming else
                  sum(matrix[code, target]["pixel_count"] for target in NAMES if target != code))
        facts.append({"start_year": start, "end_year": end, "class_code": code,
                      "label": f"{NAMES[code]}{direction}", "pixel_count": pixels,
                      "area_km2": round(pixels * .0009, 4)})
    if len(facts) == 1:
        row = facts[0]
        conclusion = f'{row["start_year"]}→{row["end_year"]} 年{NAMES[code]}{direction}面积为 {row["area_km2"]:.4f} km²。'
    else:
        peak = max(row["pixel_count"] for row in facts)
        winners = [row for row in facts if row["pixel_count"] == peak]
        label = "、".join(f'{row["start_year"]}→{row["end_year"]} 年' for row in winners)
        conclusion = f'{label}{NAMES[code]}{direction}最多，为 {peak*.0009:.4f} km²。'
    detail = "；".join(f'{row["start_year"]}→{row["end_year"]} 年 {row["area_km2"]:.4f} km²'
                      for row in facts)
    return {
        "answer": (f"结论：{conclusion}\n依据：{detail}。各项分别取起止年 CLCD 转移矩阵中"
                   f"{NAMES[code]}所在{'列' if incoming else '行'}除对角线外的像元数之和，"
                   "再乘每像元 0.0009 km²；不是全区变化面积，也不是该地类面积净变化。"),
        "facts": facts, "tool": "multi_tool", "generation_mode": "validated_flow_periods",
        "tool_trace": [{"step": "run_tool", "tool": "transition_matrix",
                        "start_year": a, "end_year": b} for a, b in periods],
        "query": {"start_year": min(years), "end_year": max(years),
                  "class_codes": [code],
                  "chart_mode": "transition" if len(periods) == 1 else "periodcompare",
                  "comparison_kind": "transition", "comparison_label": f"{NAMES[code]}各区间{direction}面积",
                  "comparison_periods": facts, "min_patch_km2": 0},
        "evidence": {"statistics": "各区间 CLCD 起止年像元转移矩阵",
                     "documents": [], "graph_paths": [], "retrieval_status": "not_needed"},
    }


def _rank_selected_annual_years(question: str):
    """指定非连续年份的同一地类面积排名；不计算地类转移。"""
    years = [int(x) for x in re.findall(r"(?<!\d)(?:19\d{2}|20[0-2]\d)(?!\d)", question)]
    # 面积是某年的存量；净变化必须先定义每一年的比较基准。
    if (len(years) >= 3 and
            any(w in question for w in ("净变化", "净增", "净减", "转入", "转出", "转化"))):
        if 1990 in years and any(w in question for w in ("净变化", "净增", "净减")):
            raise HTTPException(400, "1990 年是数据起始年，缺少 1989 年数据，不能计算 1990 年相对上一年的净变化。请写出每项起止年份，例如 1990→1991、1998→1999、2022→2023，并说明比较净变化还是转入面积。")
        raise HTTPException(400, "指定年份的净变化／转入转出需要逐项明确起止年份；请写出每个比较区间及指标，不能直接按单年面积排序。")
    if (len(years) < 3 or len(years) > 36 or len(set(years)) != len(years) or
            not any(w in question for w in ("哪一年", "哪年")) or
            not any(w in question for w in ("最多", "最少", "最大", "最小"))):
        return None
    if any(w in question for w in ("转为", "转成", "转向", "转入", "转出", "转移", "转化", "净变化", "净增", "净减", "→")):
        return None
    names = {"耕地": 1, "林地": 2, "灌丛": 3, "草地": 4, "水体": 5,
             "裸地": 7, "建设用地": 8, "不透水面": 8, "湿地": 9}
    mentioned = {code for name, code in names.items() if name in question}
    if len(mentioned) != 1:
        return None
    if min(years) < 1990 or max(years) > 2025:
        raise HTTPException(400, "指定年份必须在 1990–2025 年")
    code = next(iter(mentioned))
    from app import annual_statistics
    from agent_tools import CLASS_NAMES
    facts = []
    for year in years:
        row = next(x for x in annual_statistics(year)["classes"] if x["class_code"] == code)
        facts.append({"start_year": year, "end_year": year, "year": year,
                      "class_code": code, "label": CLASS_NAMES[code],
                      "area_km2": row["area_km2"]})
    minimum = any(w in question for w in ("最少", "最小"))
    winner = (min if minimum else max)(facts, key=lambda x: (x["area_km2"], -x["year"]))
    comparisons = "；".join(f"{r['year']} 年 {r['area_km2']:.4f} km²" for r in facts)
    return {
        "answer": (f"结论：所列年份中，{winner['year']} 年{winner['label']}面积"
                   f"{'最少' if minimum else '最多'}，为 {winner['area_km2']:.4f} km²。\n"
                   f"依据：{comparisons}。逐一读取对应年份的 CLCD 年度面积表。"),
        "facts": facts, "tool": "multi_tool",
        "generation_mode": "validated_selected_year_areas",
        "tool_trace": [{"step": "run_tool", "tool": "annual_statistics", "year": r["year"]}
                       for r in facts],
        "query": {"start_year": min(years), "end_year": max(years),
                  "class_codes": [code], "chart_mode": "periodcompare",
                  "comparison_periods": facts, "comparison_label": "指定年份地类面积对比",
                  "comparison_kind": "annual_area",
                  "min_patch_km2": 0},
        "evidence": {"statistics": "指定年份的 CLCD 年度面积表",
                     "documents": [], "graph_paths": [], "retrieval_status": "not_needed"},
    }


def _compare_two_period_flows(question: str):
    """四个年份及两个明确转移方向：分别读取两期矩阵的对应格。"""
    years = list(re.finditer(r"(?<!\d)(?:19\d{2}|20[0-2]\d)(?!\d)", question))
    if len(years) != 4 or not any(w in question for w in ("还是", "更大", "更多", "比较", "相差", "哪个", "哪种")):
        return None
    from agent_tools import CLASS_NAMES
    names = {"耕地": 1, "林地": 2, "灌丛": 3, "草地": 4, "水体": 5,
             "裸地": 7, "建设用地": 8, "不透水面": 8, "湿地": 9}
    pattern = "|".join(sorted(names, key=len, reverse=True))
    direction = rf"({pattern})\s*(?:转为|转成|转向|转|→)\s*({pattern})"
    first_text = question[years[1].end():years[2].start()]
    second_text = question[years[3].end():]
    first_matches = re.findall(direction, first_text)
    second_matches = re.findall(direction, second_text)
    if len(first_matches) != 1 or len(second_matches) != 1:
        # 四年两方向不能交给只支持“同一方向跨区间”的旧比较器。
        if len(re.findall(direction, question)) >= 2:
            raise HTTPException(400, "请按‘起始年到结束年 A转B，与起始年到结束年 C转D 比较’写明两个区间和方向")
        return None
    periods = [(int(years[0].group()), int(years[1].group())),
               (int(years[2].group()), int(years[3].group()))]
    if any(a < 1990 or b > 2025 or a >= b for a, b in periods):
        raise HTTPException(400, "两个起止年份区间须在 1990–2025 年内，且起始年早于结束年")
    from app import transition_matrix
    facts = []
    for (start, end), (source_name, target_name) in zip(periods, (first_matches[0], second_matches[0])):
        source, target = names[source_name], names[target_name]
        if source == target:
            raise HTTPException(400, "请指定两个不同地类之间的转换")
        matrix = transition_matrix(start, end)["matrix"]
        row = next(item for item in matrix
                   if item["from_code"] == source and item["to_code"] == target)
        facts.append({"start_year": start, "end_year": end,
                      "from_code": source, "to_code": target,
                      "label": f"{CLASS_NAMES[source]}→{CLASS_NAMES[target]}",
                      "pixel_count": row["pixel_count"], "area_km2": row["area_km2"]})
    a, b = facts
    conclusion = ("两者面积相同" if a["pixel_count"] == b["pixel_count"] else
                  f"{a['start_year']}→{a['end_year']} 年{a['label']}更大" if a["pixel_count"] > b["pixel_count"] else
                  f"{b['start_year']}→{b['end_year']} 年{b['label']}更大")
    answer = (f"结论：{conclusion}，相差 {abs(a['pixel_count']-b['pixel_count'])*.0009:.4f} km²。\n"
              f"依据：{a['start_year']}→{a['end_year']} 年{a['label']} {a['area_km2']:.4f} km²；"
              f"{b['start_year']}→{b['end_year']} 年{b['label']} {b['area_km2']:.4f} km²。"
              "每项均取各自起止年的 CLCD 像元叠置转移矩阵，不将相邻年图谱流量相加。")
    return {"answer": answer, "facts": facts, "tool": "multi_tool",
            "tool_trace": [{"step": "run_tool", "tool": "transition_matrix",
                            "start_year": a["start_year"], "end_year": a["end_year"]},
                           {"step": "run_tool", "tool": "transition_matrix",
                            "start_year": b["start_year"], "end_year": b["end_year"]}],
            "generation_mode": "validated_two_matrix_comparison",
            "query": {"start_year": min(a["start_year"], b["start_year"]),
                      "end_year": max(a["end_year"], b["end_year"]),
                      "class_codes": sorted({a["from_code"], a["to_code"], b["from_code"], b["to_code"]}),
                      "chart_mode": "periodcompare", "comparison_periods": facts,
                      "comparison_label": "两区间地类转换", "min_patch_km2": 0},
            "evidence": {"statistics": "两个区间各自的起止年 CLCD 像元转移矩阵",
                         "documents": [], "graph_paths": [], "retrieval_status": "not_needed"}}


def _overall_changed_area(question: str):
    """明确的全区两年变化像元面积，直接使用已核验变化区域接口。"""
    years = [int(x) for x in re.findall(r"(?<!\d)(?:19\d{2}|20[0-2]\d)(?!\d)", question)]
    if (len(years) != 2 or "面积" not in question or
            not any(w in question for w in ("发生变化的区域", "变化区域", "改变的区域")) or
            any(w in question for w in ("斑块", "转入", "转出", "超过", "平方千米以上",
                                        "耕地", "林地", "灌丛", "草地", "水体", "裸地",
                                        "建设用地", "湿地", "积雪", "冰川"))):
        return None
    if years[0] < 1990 or years[1] > 2025 or years[0] >= years[1]:
        raise HTTPException(400, "变化区域查询请写出 1990–2025 年内从早到晚的两个年份")
    from app import changed_area_statistics
    result = changed_area_statistics(years[0], years[1], 0.0)
    return {
        "answer": (f"结论：官渡区 {years[0]}→{years[1]} 年土地覆盖类型发生变化的区域面积为 "
                   f"{result['changed_area_km2']:.4f} km²。\n依据：在两个年份的原始 CLCD "
                   f"等面积网格上比较分类编码；变化 {result['changed_pixel_count']} 个像元，"
                   "每像元面积 0.0009 km²，零斑块阈值。"),
        "facts": {"changed_pixel_count": result["changed_pixel_count"],
                  "changed_area_km2": result["changed_area_km2"]},
        "generation_mode": "validated_changed_area", "tool": "multi_tool",
        "tool_trace": [{"step": "run_tool", "tool": "changed_area_statistics",
                        "start_year": years[0], "end_year": years[1]}],
        "query": {"start_year": years[0], "end_year": years[1],
                  "class_codes": list(range(1, 10)), "chart_mode": "changedareas",
                  "min_patch_km2": 0},
        "evidence": {"statistics": "官渡区两期 CLCD 像元变化及面积统计",
                     "documents": [], "graph_paths": [], "retrieval_status": "not_needed"},
    }


def _use_existing_planner(question: str) -> bool:
    """需要排序、存在性、解释、多方向比较时使用已有的完整分析流程。"""
    years = re.findall(r"(?<!\d)(?:19\d{2}|20[0-2]\d)(?!\d)", question)
    # 两个起止年份的转入/转出由 LangGraph 两次矩阵查询处理。
    if len(years) == 4 and ("转入" in question or "转出" in question):
        return False
    phrases = (
        "哪一年", "哪两年", "哪段时间", "什么时候", "何时", "最多", "最少",
        "最大", "最小", "最高", "最低", "排名", "主要转成", "主要转为",
        "有没有", "是否有", "存在吗", "有冰川", "为什么", "原因", "导致",
        "造成", "驱动", "依据", "来源", "文献", "资料", "规划",
        "是否因为", "能否证明", "哪种地类", "哪一类", "总共净", "累计净",
        "占比", "比例", "百分比", "转出", "转入", "斑块数", "超过",
    )
    # 同一区间的两条有向转移必须读取并对比两格矩阵。
    directed = re.findall(r"(?:转为|转成|转向|→)\s*(?:耕地|林地|草地|水体|建设用地|湿地|裸地)", question)
    return any(phrase in question for phrase in phrases) or len(directed) >= 2 or not years


def answer_agent_query(question: str) -> dict:
    """优先走与问题匹配的现成操作；工具协议失败时再尝试安全的广覆盖规划器。"""
    net_periods = _compare_explicit_net_periods(question)
    if net_periods is not None:
        return net_periods
    outgoing = _compare_explicit_outgoing_periods(question)
    if outgoing is not None:
        return outgoing
    extended = answer_extended(question)
    if extended is not None:
        return extended
    selected_years = _rank_selected_annual_years(question)
    if selected_years is not None:
        return selected_years
    two_flows = _compare_two_period_flows(question)
    if two_flows is not None:
        return two_flows
    changed_area = _overall_changed_area(question)
    if changed_area is not None:
        return changed_area
    if (re.search(r"(?:面积)?净变化", question) and
            any(word in question for word in ("什么意思", "是什么", "怎么理解", "如何计算")) and
            not re.search(r"(?<!\d)(?:19\d{2}|20[0-2]\d)(?!\d)", question)):
        return {
            "answer": ("结论：面积净变化是结束年份的某类土地覆盖面积减去起始年份的面积。"
                       "\n依据：结果为正表示该类净增加，为负表示净减少，单位通常为 km²。"
                       "它不能表示这一地类转入或转出的总面积；若要分别计算转入、转出，"
                       "须叠置两个年份的分类像元并查询转移矩阵。"),
            "query": None, "evidence": {"documents": [], "graph_paths": [],
                                           "retrieval_status": "概念定义无需文献检索"},
            "generation_mode": "validated_definition",
            "tool_trace": [{"step": "definition", "term": "面积净变化"}],
        }
    from agent_graph_full import answer_with_full_graph
    from smart_query import answer_question

    if any(word in question for word in ("同时", "并且", "以及")):
        from agent_multi_step import answer_multi
        try:
            return answer_multi(question)
        except HTTPException as exc:
            if exc.status_code != 422:
                raise
            # 组合问题不能回落到“恰好一个工具”的旧工作流。
            try:
                result = answer_question(question)
            except HTTPException:
                raise exc
            result["tool_trace"] = [{"step": "route", "path": "existing_planner",
                                     "note": "多工具规划未通过；由统计规划器处理"}]
            result["generation_mode"] = "agent_planner_" + result.get("generation_mode", "local")
            return result

    if _use_existing_planner(question):
        result = answer_question(question)
        result["tool_trace"] = [{"step": "route", "path": "existing_planner",
                                  "note": "模型规划后由本地分析函数计算"}]
        result["generation_mode"] = "agent_planner_" + result.get("generation_mode", "local")
        return result
    try:
        return answer_with_full_graph(question)
    except HTTPException as exc:
        # 422 表示工具调用格式或参数无法执行，改用已有且独立校验的查询规划器。
        # 503 表示服务故障，不能伪装为一次成功的兜底查询。
        if exc.status_code != 422:
            raise
        try:
            result = answer_question(question)
        except HTTPException:
            raise exc
        result["tool_trace"] = [{"step": "route", "path": "existing_planner",
                                  "note": "LangGraph 工具参数未通过；使用现有规划器复核查询"}]
        result["generation_mode"] = "agent_planner_" + result.get("generation_mode", "local")
        return result
