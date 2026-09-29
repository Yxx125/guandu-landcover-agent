"""测试八工具 LangGraph 工作流；需要 Ollama 和 FastAPI 服务运行。"""

import sys
from pathlib import Path

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent_graph_full import answer_with_full_graph


CASES = [
    ("官渡区 2025 年耕地面积是多少？", "get_annual_area"),
    ("官渡区 2010 到 2020 年耕地转建设用地面积是多少？", "get_transition_matrix"),
    ("比较官渡区 2010 和 2020 年各类土地覆盖面积变化", "compare_years"),
    ("官渡区 2020 到 2025 年耕地和建设用地逐年面积趋势", "get_area_timeseries"),
    ("官渡区 2020 到 2025 年建设用地逐年净变化", "get_yearly_net_change"),
    ("官渡区 2010 到 2020 年变化斑块，阈值 0.1 km²", "get_changed_areas"),
    ("坐标经度 102.82251684 纬度 25.04749259 在官渡区 2010 到 2020 年的点位地类历史", "get_point_history"),
    ("查询官渡区 2025 年耕地、林地、建设用地的知识图谱关系", "get_knowledge_graph"),
]


def main():
    failed = []
    for question, expected in CASES:
        print(f"\n问题：{question}", flush=True)
        try:
            result = answer_with_full_graph(question)
            if result["tool"] != expected:
                raise ValueError(f"选错工具，预期 {expected}，实际 {result['tool']}")
            if "结论：" not in result["answer"] or "依据：" not in result["answer"]:
                raise ValueError("答案缺少结论或依据")
            print(f"[通过] 工具：{result['tool']}；参数：{result['arguments']}", flush=True)
            print(result["answer"][:450], flush=True)
        except (HTTPException, ValueError, KeyError, RuntimeError) as exc:
            failed.append(expected)
            print(f"[失败] {expected}: {getattr(exc, 'detail', str(exc))}", flush=True)
    print(f"\n第 06 步验收：{len(CASES) - len(failed)}/{len(CASES)} 道问题通过。")
    if failed:
        print("未通过：" + "、".join(failed))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
