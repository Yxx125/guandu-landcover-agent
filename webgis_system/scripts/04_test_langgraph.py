"""独立运行 LangGraph 两个工具调用示例，不改变现有网页。"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent_graph import answer_with_graph


QUESTIONS = (
    "官渡区 2025 年耕地面积是多少？",
    "官渡区 2010 到 2020 年耕地转建设用地面积是多少？",
)


def main():
    for question in QUESTIONS:
        result = answer_with_graph(question)
        print("\n问题：", question)
        print(result["answer"])
        print("节点记录：", json.dumps(result["tool_trace"], ensure_ascii=False))
    print("\n通过：两道问题完成 LangGraph 节点流程，且回答数值通过工具结果核对。")


if __name__ == "__main__":
    main()
