"""逐项验收新增只读工具；需要本地 FastAPI、数据库、栅格和 Neo4j。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent_tools import execute_tool


CASES = [
    ("compare_years", {"start_year": 2010, "end_year": 2020}, "changes"),
    ("get_area_timeseries", {"start_year": 2020, "end_year": 2025,
                             "class_codes": [1, 8]}, "series"),
    ("get_yearly_net_change", {"start_year": 2020, "end_year": 2025,
                               "class_code": 8}, "data"),
    ("get_changed_areas", {"start_year": 2010, "end_year": 2020,
                           "min_patch_km2": 0.1}, "patch_count"),
    ("get_point_history", {"lon": 102.82251684, "lat": 25.04749259,
                           "start_year": 2010, "end_year": 2020}, "history"),
    ("get_knowledge_graph", {"start_year": 2025, "end_year": 2025,
                             "class_codes": [1, 2, 8]}, "nodes"),
]


def main():
    failed = []
    for name, arguments, key in CASES:
        try:
            result = execute_tool(name, arguments)
            if key not in result or result[key] is None:
                raise ValueError(f"结果缺少 {key}")
            value = result[key]
            summary = f"{key}={len(value)} 条" if isinstance(value, list) else f"{key}={value}"
            print(f"[通过] {name}: {summary}", flush=True)
        except (ValueError, RuntimeError, KeyError) as exc:
            failed.append(name)
            print(f"[失败] {name}: {exc}", flush=True)
    print(f"\n验收：{len(CASES)-len(failed)}/{len(CASES)} 个工具通过。")
    if failed:
        print("待排查工具：" + "、".join(failed))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
