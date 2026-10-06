from __future__ import annotations

import json
from typing import Any

from .index import NgramIndex
from .local_model import LocalModel


QA_SYSTEM = """你是城市基础设施定检报告问答智能体。
只根据提供的证据回答，不得使用外部知识，不得猜测，不得把不同桥梁或年份混在一起。
严格输出 JSON：answer（字符串）、confidence（high/medium/low）、evidence_ids（字符串数组）。
如果证据不足，answer 写“无法根据现有报告证据确定”，confidence 写 low。
"""


def answer_question(question: str, index: NgramIndex, model: LocalModel, limit: int = 12) -> dict[str, Any]:
    evidence = index.search(question, limit=limit)
    if not evidence:
        return {"status": "no_evidence", "answer": "无法根据现有报告证据确定", "confidence": "low", "evidence": []}
    context = "\n".join(f"[{item.source_id}] {item.quote}" for item in evidence)
    user = json.dumps({"question": question, "evidence": context}, ensure_ascii=False)
    try:
        data, metadata = model.generate_json(QA_SYSTEM, user)
        valid_ids = {item.source_id for item in evidence}
        ids = [item for item in data.get("evidence_ids", []) if item in valid_ids]
        return {
            "status": "success",
            "answer": str(data.get("answer", "无法根据现有报告证据确定")),
            "confidence": data.get("confidence", "low") if data.get("confidence") in {"high", "medium", "low"} else "low",
            "evidence": [{**item.to_dict()} for item in evidence if item.source_id in ids],
            "model": metadata,
        }
    except Exception as exc:
        return {"status": "blocked", "answer": "模型不可用，未生成答案", "confidence": "low", "evidence": [], "error": str(exc), "model": model.metadata}

