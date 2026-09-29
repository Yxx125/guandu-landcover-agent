"""独立验证工具包装层是否能从正在运行的 FastAPI 读取真实数据。"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent_tools import execute_tool


def main():
    annual = execute_tool("get_annual_area", {"year": 2025})
    cropland = next(row for row in annual["classes"] if row["class_code"] == 1)
    print("年度面积工具：", json.dumps({"year": 2025, "cropland": cropland}, ensure_ascii=False))

    matrix = execute_tool("get_transition_matrix", {"start_year": 2010, "end_year": 2020})
    flow = next((row for row in matrix["flows"]
                 if row["from_code"] == 1 and row["to_code"] == 8), None)
    area = 0 if flow is None else flow["area_km2"]
    print("转移矩阵工具：", json.dumps({"period": "2010→2020",
                                     "cropland_to_builtup_km2": area}, ensure_ascii=False))
    print("通过：两个只读工具均从本地 FastAPI 取得真实结果。")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, KeyError, StopIteration) as exc:
        print(f"检测失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc
