r"""第 12 步：组合问法回归；须先启动 8001 后端及数据库。

PowerShell: .\.venv\Scripts\python.exe .\scripts\12_query_regression.py
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


ROOT = Path(__file__).resolve().parent.parent
BASE = "http://127.0.0.1:8001"


def ask(question):
    request = Request(BASE + "/api/agent-full/ask",
                      json.dumps({"question": question}, ensure_ascii=False).encode("utf-8"),
                      {"Content-Type": "application/json; charset=utf-8"}, method="POST")
    try:
        with urlopen(request, timeout=180) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def matrix_area(start, end, source, target):
    with urlopen(f"{BASE}/api/stats/transitions?start_year={start}&end_year={end}", timeout=180) as response:
        matrix = json.load(response)["matrix"]
    row = next(x for x in matrix if x["from_code"] == source and x["to_code"] == target)
    return row["pixel_count"]


def verify_cross(question, expected):
    code, result = ask(question)
    assert code == 200, f"HTTP {code}: {result}"
    assert result.get("generation_mode") == "validated_two_matrix_comparison", result.get("generation_mode")
    facts = result["facts"]
    actual = [(r["start_year"], r["end_year"], r["from_code"], r["to_code"]) for r in facts]
    assert actual == expected, f"方向/年份配对错误：{actual} != {expected}"
    for fact in facts:
        pixels = matrix_area(fact["start_year"], fact["end_year"], fact["from_code"], fact["to_code"])
        assert pixels == fact["pixel_count"], "回答对应的矩阵格与接口不同"
    a, b = facts
    expected_larger = a if a["pixel_count"] > b["pixel_count"] else b
    if a["pixel_count"] != b["pixel_count"]:
        assert f"{expected_larger['start_year']}→{expected_larger['end_year']} 年{expected_larger['label']}更大" in result["answer"]
    assert result["query"]["chart_mode"] == "periodcompare"
    assert len(result["query"]["comparison_periods"]) == 2
    return result["answer"]


def main():
    results = []

    def case(name, fn):
        try:
            detail = fn()
            results.append({"name": name, "status": "PASS", "detail": str(detail)[:500]})
            print(f"[PASS] {name}：{str(detail)[:240]}")
        except Exception as exc:
            results.append({"name": name, "status": "FAIL", "detail": str(exc)[:500]})
            print(f"[FAIL] {name}：{exc}")

    case("两区间两方向例一", lambda: verify_cross(
        "2010到2020年耕地转为建设用地大还是2021到2022年林地转为建设用地大",
        [(2010, 2020, 1, 8), (2021, 2022, 2, 8)]))
    case("两区间两方向例二（转）", lambda: verify_cross(
        "2020到2021年耕地转林地大还是2000到2021年耕地转建设用地大",
        [(2020, 2021, 1, 2), (2000, 2021, 1, 8)]))

    def selected_years():
        code, result = ask("2025年、2023年、2022年，这三年哪一年建设用地最多")
        assert code == 200, f"HTTP {code}: {result.get('detail', result)}"
        assert result.get("generation_mode") == "validated_selected_year_areas"
        facts = result["facts"]
        assert [(x["start_year"], x["end_year"]) for x in facts] == [
            (2025, 2025), (2023, 2023), (2022, 2022)]
        for item in facts:
            with urlopen(f"{BASE}/api/stats/annual?year={item['year']}", timeout=90) as response:
                annual = json.load(response)["classes"]
            expected = next(x["area_km2"] for x in annual if x["class_code"] == 8)
            assert item["area_km2"] == expected
        winner = max(facts, key=lambda x: (x["area_km2"], -x["year"]))
        assert f"{winner['year']} 年" in result["answer"]
        assert result["query"]["chart_mode"] == "periodcompare"
        assert result["query"]["comparison_kind"] == "annual_area"
        return result["answer"]
    case("非连续三年份单年面积最大", selected_years)

    def different_outgoing_classes():
        code, result = ask("2023到2025年的耕地转出多还是2016年到2025年林地转出的多")
        assert code == 200, f"HTTP {code}: {result.get('detail', result)}"
        assert result.get("generation_mode") == "validated_flow_periods"
        facts = result["facts"]
        assert [(x["start_year"], x["end_year"], x["class_code"]) for x in facts] == [
            (2023, 2025, 1), (2016, 2025, 2)]
        for fact in facts:
            pixels = sum(matrix_area(fact["start_year"], fact["end_year"],
                                     fact["class_code"], target)
                         for target in range(1, 10) if target != fact["class_code"])
            assert pixels == fact["pixel_count"], "转出面积与原始转移矩阵不一致"
        larger = max(facts, key=lambda row: row["pixel_count"])
        assert larger["label"] in result["answer"]
        assert result["query"]["class_codes"] == [1, 2]
        return result["answer"]
    case("两区间两地类总转出比较", different_outgoing_classes)

    def supported(name, question, start, end, words=()):
        def verify():
            code, result = ask(question)
            assert code == 200, f"HTTP {code}: {result.get('detail', result)}"
            query = result.get("query") or {}
            assert (query.get("start_year"), query.get("end_year")) == (start, end), query
            assert all(word in result.get("answer", "") for word in words), result.get("answer")
            return f"{start}→{end}；{result.get('generation_mode')}；{result['answer'][:90]}"
        case(name, verify)

    supported("单年面积", "官渡区2025年耕地面积是多少", 2025, 2025, ("耕地",))
    supported("两年面积净变化", "官渡区2010到2020年耕地面积净变化是多少", 2010, 2020, ("耕地",))
    supported("逐年面积趋势", "查看官渡区2020到2025年耕地和林地逐年面积变化", 2020, 2025)
    supported("逐年净增减", "官渡区2020到2025年建设用地每年净增减多少", 2020, 2025)
    supported("起止年转移", "官渡区2010到2020年耕地转为建设用地面积是多少", 2010, 2020, ("耕地",))
    supported("变化区域", "官渡区2010到2020年土地覆盖发生变化的区域面积是多少", 2010, 2020)
    supported("双工具组合", "同时查询官渡区2020年耕地面积，以及2010到2020年耕地面积净变化", 2010, 2020, ("耕地",))
    supported("图谱与文档解释", "官渡区2010到2020年耕地面积变化是多少？请结合图谱和资料说明依据", 2010, 2020)

    def concept():
        code, result = ask("面积净变化是什么意思")
        assert code == 200 and result.get("query") is None
        assert "结束年份" in result["answer"] and "起始年份" in result["answer"]
        return "概念说明不更改地图条件"
    case("无年份概念问答", concept)

    def ambiguous():
        code, result = ask("2020到2021年耕地转林地和林地转建设用地，2000到2021年哪个更大")
        assert code in (400, 422), f"含糊的区间配对不应计算：HTTP {code}"
        return f"HTTP {code}：{result.get('detail', '')}"
    case("歧义拒绝", ambiguous)

    def invalid_range():
        code, result = ask("官渡区2025到2020年耕地转为建设用地多少")
        assert code in (400, 422), f"倒序年份不应计算：HTTP {code}"
        return f"HTTP {code}：{result.get('detail', '')}"
    case("倒序年份拒绝", invalid_range)

    def model_failure_boundary():
        # 只验证模型不可用时允许的严格解析边界，不冒充实际断网验收。
        import sys
        sys.path.insert(0, str(ROOT))
        from smart_query import _fallback_for_two_flows
        q = "2010到2020年耕地转为建设用地和林地转为建设用地，哪种转移面积更大"
        assert _fallback_for_two_flows(q) is not None
        assert _fallback_for_two_flows("2010到2020年什么转为什么更大") is None
        return "仅明确两方向可离线解析；未测试真实云端断网"
    case("模型故障边界（解析）", model_failure_boundary)

    report = {"tested_at": datetime.now().astimezone().isoformat(), "cases": results,
              "passed": sum(x["status"] == "PASS" for x in results),
              "failed": sum(x["status"] == "FAIL" for x in results)}
    dest = ROOT / "reports" / f"12_query_regression_{datetime.now():%Y%m%d_%H%M%S}.json"
    dest.parent.mkdir(exist_ok=True)
    dest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"结果：{report['passed']} 通过、{report['failed']} 失败；报告：{dest}")
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
