r"""第 10 步：在用户本机验收 FastAPI、数据、图谱与 Agent 的真实调用链。

PowerShell: .\.venv\Scripts\python.exe .\scripts\10_acceptance.py
运行前启动 PostgreSQL、GeoServer、Neo4j、FastAPI，以及当前配置的模型服务。
"""

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


ROOT = Path(__file__).resolve().parent.parent


def get(base, path, *, timeout=90, **params):
    url = base + path + ("?" + urlencode(params) if params else "")
    return request(url, timeout=timeout)


def post(base, path, question):
    body = json.dumps({"question": question}, ensure_ascii=False).encode("utf-8")
    req = Request(base + path, data=body,
                  headers={"Content-Type": "application/json; charset=utf-8"},
                  method="POST")
    return request(req, timeout=180)


def request(url, timeout=90):
    try:
        with urlopen(url, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as exc:
        payload = exc.read(500).decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {payload}") from exc
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise RuntimeError(f"接口不可用或返回非 JSON：{type(exc).__name__}: {exc}") from exc


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def run(base, skip_llm, require_model_tool_calls=False):
    entries = []
    cache = {}

    def case(name, action):
        try:
            detail = action()
            entries.append({"name": name, "status": "PASS", "detail": detail})
            print(f"[PASS] {name}：{detail}")
        except Exception as exc:
            entries.append({"name": name, "status": "FAIL", "detail": str(exc)})
            print(f"[FAIL] {name}：{exc}")

    def years_and_maps():
        years = get(base, "/api/years")["years"]
        check(years == list(range(1990, 2026)), "应为 1990–2025 连续 36 年")
        maps = get(base, "/api/maps", start_year=2010, end_year=2020)
        check(maps["years"] == list(range(2010, 2021)), "地图年份不完整")
        check(len(maps["annual_layers"]) == 11 and maps["change_layer"],
              "年份地图或变化图层缺失")
        return "36 年完整；2010–2020 地图元数据 11 层"

    def annual():
        first = get(base, "/api/stats/annual", year=2010)
        last = get(base, "/api/stats/annual", year=2020)
        for row in (first, last):
            classes = row["classes"]
            check(len(classes) == 9 and {r["class_code"] for r in classes} == set(range(1, 10)),
                  f"{row['year']} 年类别不全")
            check(math.isclose(sum(r["area_km2"] for r in classes), row["total_area_km2"], abs_tol=.0002),
                  "年度总面积与地类求和不一致")
        cache["annual"] = (first, last)
        return f"2010、2020 各九类；全区面积 {first['total_area_km2']}、{last['total_area_km2']} km²"

    def compare():
        first, last = cache.get("annual") or (
            get(base, "/api/stats/annual", year=2010),
            get(base, "/api/stats/annual", year=2020))
        result = get(base, "/api/stats/compare", start_year=2010, end_year=2020)
        a = {r["class_code"]: r["area_km2"] for r in first["classes"]}
        b = {r["class_code"]: r["area_km2"] for r in last["classes"]}
        check(len(result["changes"]) == 9, "两年比较未返回九类")
        for row in result["changes"]:
            check(math.isclose(row["net_change_km2"], b[row["class_code"]] - a[row["class_code"]], abs_tol=.00011),
                  f"地类 {row['class_code']} 净变化不等于结束年减起始年")
        cache["compare"] = result
        return "九类净变化与两年面积差逐一一致"

    def transitions():
        matrix = get(base, "/api/stats/transitions", start_year=2010, end_year=2020)
        check(len(matrix["matrix"]) == 81, "转移矩阵不是 9×9")
        changed = sum(row["pixel_count"] for row in matrix["matrix"]
                      if row["from_code"] != row["to_code"])
        check(changed == matrix["changed_pixel_count"], "变化像元与矩阵非对角线不一致")
        check(math.isclose(matrix["changed_area_km2"], changed * .0009, abs_tol=.00011),
              "变化面积与 30 米像元数量不一致")
        cache["transition"] = matrix
        return f"81 格；变化 {changed} 像元、{matrix['changed_area_km2']} km²"

    def changed():
        result = get(base, "/api/stats/changed-areas", start_year=2010,
                     end_year=2020, min_patch_km2=0)
        matrix = cache.get("transition") or get(
            base, "/api/stats/transitions", start_year=2010, end_year=2020)
        check(result["changed_pixel_count"] == matrix["changed_pixel_count"],
              "零阈值变化像元与转移矩阵不一致")
        check(len(result["classes"]) == 9, "转入转出类别不完整")
        return "零阈值面积与矩阵一致；九类转入转出存在"

    def graph():
        data = get(base, "/api/graph", start_year=2010, end_year=2020, class_codes="1,8")
        check(bool(data["nodes"]) and bool(data["links"]), "图谱没有节点或边")
        check(data["scope"] == "adjacent_year_changes", "多年图谱语义错误")
        return f"Neo4j 图谱 {len(data['nodes'])} 节点、{len(data['links'])} 关系"

    def agent():
        question = "同时查询官渡区2020年耕地面积，以及2010到2020年耕地面积净变化"
        result = post(base, "/api/agent-full/ask", question)
        runs = [item for item in result.get("tool_trace", []) if item.get("step") == "run_tool"]
        check(len(runs) >= 2, "没有完成至少两个真实工具调用；可能回退到规划器")
        check(result.get("generation_mode") in {"agent_multi_cloud", "agent_multi_ollama", "agent_multi_policy"},
              "返回模式不是多工具 Agent")
        if require_model_tool_calls:
            choices = [item for item in result.get("tool_trace", []) if item.get("step") == "choose"]
            check(len(choices) >= 2 and not any(
                item.get("step") == "policy_select" for item in result.get("tool_trace", [])),
                "两项查询未全部由模型 Function Calling 选择；检查当前模型的工具调用支持")
        check("耕地" in result.get("answer", "") and result.get("query"), "答案或地图联动参数缺失")
        return f"{result['generation_mode']}；实际执行 {len(runs)} 次工具"

    def graphrag_graph():
        evidence = get(base, "/api/evidence", question="官渡区2010到2020年耕地变化有什么资料依据？",
                       start_year=2010, end_year=2020, class_codes="1", source="graph")
        check(evidence.get("graph_paths"), f"未检索到图谱路径：{evidence.get('graph_status')}")
        return f"图谱路径 {len(evidence['graph_paths'])} 条"

    def graphrag_documents():
        print("文档检索首次加载嵌入模型可能超过 90 秒，请保持后端运行……", flush=True)
        evidence = get(base, "/api/evidence", question="官渡区耕地变化有什么资料依据？",
                       start_year=2010, end_year=2020, class_codes="1", source="documents",
                       timeout=180)
        check(evidence.get("documents"), f"未检索到文档片段：{evidence.get('retrieval_status')}")
        return f"文档片段 {len(evidence['documents'])} 条"

    for name, action in (
        ("年份与地图", years_and_maps), ("年度面积", annual),
        ("两年比较", compare), ("转移矩阵", transitions),
        ("变化区域", changed), ("知识图谱", graph),
    ):
        case(name, action)
    if skip_llm:
        print("[SKIP] Agent、GraphRAG 模型测试（--skip-llm）")
    else:
        case("多工具 Agent", agent)
        case("GraphRAG 图谱证据", graphrag_graph)
        case("GraphRAG 文档证据", graphrag_documents)
    return entries


def main():
    parser = argparse.ArgumentParser(description="官渡区系统端到端验收")
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--skip-llm", action="store_true")
    parser.add_argument("--require-model-tool-calls", action="store_true",
                        help="严格验证两次工具调用均由模型选择，不接受规则回退")
    args = parser.parse_args()
    entries = run(args.base_url.rstrip("/"), args.skip_llm, args.require_model_tool_calls)
    report = {"tested_at": datetime.now().astimezone().isoformat(),
              "base_url": args.base_url, "checks": entries,
              "passed": sum(row["status"] == "PASS" for row in entries),
              "failed": sum(row["status"] == "FAIL" for row in entries),
              "require_model_tool_calls": args.require_model_tool_calls,
              "note": "本脚本检查接口及数值不变量；地图渲染、配色和图表排版仍需浏览器目视验收。"}
    folder = ROOT / "reports"
    folder.mkdir(exist_ok=True)
    path = folder / ("10_acceptance_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".json")
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n验收结果：{report['passed']} 通过，{report['failed']} 失败；报告：{path}")
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
