r"""第 11 步：验证解释性问答真实使用图谱与文档证据。

PowerShell: .\.venv\Scripts\python.exe .\scripts\11_evidence_answer_acceptance.py
需先运行 FastAPI、Neo4j、Chroma 索引及当前配置的模型。
"""

import argparse
import json
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


QUESTION = "官渡区2010到2020年耕地面积变化是多少？请结合图谱和资料说明依据。"
ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description="解释性问答证据链验收")
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    args = parser.parse_args()
    request = Request(
        args.base_url.rstrip("/") + "/api/agent-full/ask",
        data=json.dumps({"question": QUESTION}, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"}, method="POST",
    )
    try:
        with urlopen(request, timeout=240) as response:
            result = json.load(response)
    except HTTPError as exc:
        print(f"[FAIL] HTTP {exc.code}：{exc.read(500).decode('utf-8', 'replace')}")
        return 1
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        print(f"[FAIL] 接口不可用：{type(exc).__name__}: {exc}")
        return 1

    evidence = result.get("evidence") or {}
    paths = evidence.get("graph_paths") or []
    documents = evidence.get("documents") or []
    answer = result.get("answer") or ""
    mode = result.get("generation_mode") or ""
    checks = {
        "回答以直接结论开头": answer.startswith("结论：") and "依据：" in answer,
        "返回图谱路径": len(paths) > 0 and evidence.get("graph_status", "").startswith("ok"),
        "返回文档片段及来源": len(documents) > 0 and all(
            item.get("source") and item.get("text") for item in documents),
        "当前模型实际生成解释": mode.endswith("_cloud") or mode.endswith("_ollama"),
        "查询条件联动地图": (result.get("query") or {}).get("start_year") == 2010
                          and (result.get("query") or {}).get("end_year") == 2020,
    }
    report = {
        "tested_at": datetime.now().astimezone().isoformat(), "question": QUESTION,
        "checks": checks, "generation_mode": mode, "graph_paths": len(paths),
        "documents": [{"source": x.get("source"), "chunk": x.get("chunk")}
                      for x in documents],
        "answer": answer, "graph_status": evidence.get("graph_status"),
        "retrieval_status": evidence.get("retrieval_status"),
        "note": "验证证据传入问答和模型实际生成；文本事实是否与来源逐句一致仍须人工核查。",
    }
    for name, passed in checks.items():
        print(f"[{'PASS' if passed else 'FAIL'}] {name}")
    print(f"生成模式：{mode}；图谱路径：{len(paths)}；文档片段：{len(documents)}")
    print("回答：", answer[:1000])
    output = ROOT / "reports" / f"11_evidence_answer_{datetime.now():%Y%m%d_%H%M%S}.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("报告：", output)
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
