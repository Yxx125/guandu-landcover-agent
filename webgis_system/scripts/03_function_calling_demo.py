r"""本地 Ollama Function Calling 闭环演示。

从项目根目录运行：
.\.venv\Scripts\python.exe .\scripts\03_function_calling_demo.py
依赖：本地 Ollama、本项目 FastAPI 127.0.0.1:8001。
不修改网页与数据库；日志保存在 logs/function_calling_trace.jsonl。
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from agent_tools import TOOL_SCHEMAS, execute_tool
from model_gateway import chat as model_chat

EXAMPLES = (
    "官渡区 2025 年耕地面积是多少？",
    "官渡区 2010 到 2020 年耕地转建设用地面积是多少？",
)


def chat(messages):
    return model_chat(messages, tools=TOOL_SCHEMAS, timeout=120)


def run(question):
    messages = [
        {"role": "system", "content": "你是官渡区土地覆盖数据助手。需要数值时必须调用工具。只能引用工具返回的数值，面积保留四位小数，不要猜测。单位为 km²。"},
        {"role": "user", "content": question},
    ]
    trace = []
    for round_number in range(1, 4):
        reply = chat(messages)
        calls = reply.get("tool_calls") or []
        if not calls:
            if not trace:
                raise RuntimeError("模型未调用工具；本次演示不接受直接猜测的回答")
            answer = str(reply.get("content") or "").strip()
            if not answer:
                raise RuntimeError("模型收到工具结果后没有生成回答")
            if question == EXAMPLES[0]:
                row = next(r for r in trace[-1]["result"]["classes"] if r["class_code"] == 1)
                expected_area = row["area_km2"]
            else:
                row = next((r for r in trace[-1]["result"]["flows"]
                            if r["from_code"] == 1 and r["to_code"] == 8), None)
                expected_area = row["area_km2"] if row else 0
            if f"{expected_area:.4f}" not in answer:
                raise RuntimeError("模型回答未包含工具证据的四位小数面积，请检查模型回答；本次不记为通过")
            return answer, trace
        messages.append(reply)
        for call in calls:
            function = call.get("function") or {}
            name = function.get("name")
            raw_arguments = function.get("arguments", {})
            try:
                arguments = (json.loads(raw_arguments) if isinstance(raw_arguments, str)
                             else raw_arguments)
                # 这个演示只接受与两个固定问题相符的工具和年份，防止错误调用被当作成功。
                expected = ({"get_annual_area": {"year": 2025}}
                            if question == EXAMPLES[0] else
                            {"get_transition_matrix": {"start_year": 2010, "end_year": 2020}})
                if name not in expected or arguments != expected[name]:
                    raise ValueError(f"模型选错工具或年份：{name} {arguments}")
                result = execute_tool(name, arguments)
            except (ValueError, RuntimeError, TypeError, KeyError) as exc:
                raise RuntimeError(f"工具调用未通过校验：{exc}") from exc
            record = {"step": len(trace) + 1, "tool": name,
                      "arguments": arguments, "result": result}
            trace.append(record)
            print(f"第 {round_number} 轮调用：{name} {json.dumps(arguments, ensure_ascii=False)}")
            if name == "get_annual_area":
                cropland = next(row for row in result["classes"] if row["class_code"] == 1)
                print(f"工具证据：2025 年耕地 {cropland['area_km2']} km²")
            else:
                match = next((row for row in result["flows"]
                              if row["from_code"] == 1 and row["to_code"] == 8), None)
                print(f"工具证据：耕地→建设用地 {match['area_km2'] if match else 0} km²")
            messages.append({"role": "tool", "tool_name": name,
                             "content": json.dumps(result, ensure_ascii=False)})
    raise RuntimeError("模型连续多轮调用工具，已达到三轮限制")


def main():
    log_path = ROOT / "logs" / "function_calling_trace.jsonl"
    print(f"模型：{MODEL}。两个示例问题逐一测试；工具只读取本地 FastAPI。")
    for question in EXAMPLES:
        print("\n问题：", question)
        answer, trace = run(question)
        print("模型回答：", answer)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as output:
            output.write(json.dumps({"timestamp": datetime.now(timezone.utc).isoformat(),
                                     "model": MODEL, "question": question,
                                     "calls": trace, "model_answer": answer},
                                    ensure_ascii=False) + "\n")
    print(f"\n完成：模型选工具→Python 执行→结果回传→模型回答。调用记录：{log_path}")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, KeyError, StopIteration) as exc:
        print(f"演示停止：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc
