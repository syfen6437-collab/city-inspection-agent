from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .document_parser import ParsedReport
from .pipeline import load_reports
from .schema import PredictionRecord


def _normalize_header(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def parse_label(report: ParsedReport) -> dict[str, Any]:
    result: dict[str, Any] = {"summary": {}, "diseases": [], "recommendations": []}
    for block in report.blocks:
        cells = [item.strip() for item in block.text.split("|")]
        if len(cells) < 2:
            continue
        header = _normalize_header(cells[0])
        if header in {"字段", "序号"}:
            continue
        if len(cells) >= 3 and header in {"桥梁名称", "报告编号", "检测日期", "总体技术状况评分", "总体技术状况等级", "总体评分", "总体等级"}:
            result["summary"][cells[0]] = cells[1]
        elif len(cells) >= 4 and any(token in cells[1] for token in ("建议", "维护", "预防")):
            result["recommendations"].append({"category": cells[1], "content": cells[2], "location": cells[3]})
        elif len(cells) >= 4 and any(token in cells[1] for token in ("构件", "结构", "桥面", "支座", "桥台", "桥墩")):
            result["diseases"].append({"location": cells[1], "disease_type": cells[2], "description": cells[3]})
    return result


def match_label_filename(file_name: str) -> str:
    name = Path(file_name).stem
    name = re.sub(r"[-_（(]?(?:含有|无|含)[^）)]*(?:信息提取报告)?[）)]?$", "", name)
    name = re.sub(r"[-_ ]?信息提取报告$", "", name)
    return re.sub(r"\s+", "", name).lower()


def _field_match(prediction: dict[str, Any], labels: dict[str, Any]) -> dict[str, float]:
    summary = prediction.get("prediction") or {}
    label_summary = labels.get("summary") or {}
    comparisons = []
    for key, value in label_summary.items():
        if not value:
            continue
        candidates = [
            summary.get("bridge_name"),
            summary.get("report_number"),
            summary.get("inspection_date"),
            summary.get("overall_score"),
            summary.get("overall_grade"),
        ]
        comparisons.append(any(str(value).strip() in str(candidate) for candidate in candidates if candidate is not None))
    summary_accuracy = sum(comparisons) / len(comparisons) if comparisons else 0.0
    predicted_diseases = prediction.get("diseases") or []
    label_diseases = labels.get("diseases") or []
    matched_diseases = 0
    for label in label_diseases:
        label_text = " ".join(str(label.get(key) or "") for key in ("location", "disease_type", "description"))
        if label_text.strip() and any(
            all(token in " ".join(str(item.get(key) or "") for key in ("location", "disease_type", "description")) for token in label_text.split() if token)
            for item in predicted_diseases
        ):
            matched_diseases += 1
    disease_recall = matched_diseases / len(label_diseases) if label_diseases else 0.0
    evidence_count = len(prediction.get("evidence") or [])
    evidence_coverage = min(1.0, evidence_count / max(1, len(predicted_diseases))) if predicted_diseases else 0.0
    return {
        "summary_field_accuracy": summary_accuracy,
        "disease_record_recall": disease_recall,
        "evidence_coverage": evidence_coverage,
    }


def evaluate_predictions(source: str | Path, predictions_path: str | Path, cache_dir: str | Path, limit: int | None = None) -> dict[str, Any]:
    predictions = json.loads(Path(predictions_path).read_text(encoding="utf-8"))
    reports = load_reports(source, "train", limit)
    labels = load_reports(source, "labels", limit)
    label_map = {match_label_filename(report.file_name): parse_label(report) for report in labels}
    scores = []
    for record in predictions.get("records", []):
        if record.get("status") != "success":
            continue
        key = match_label_filename(record["file_name"])
        if key in label_map:
            scores.append(_field_match(record, label_map[key]))
    metric = {
        "train_reports": len(reports),
        "label_reports": len(labels),
        "matched": len(scores),
        "summary_field_accuracy": round(sum(item["summary_field_accuracy"] for item in scores) / len(scores), 4) if scores else 0.0,
        "disease_record_recall": round(sum(item["disease_record_recall"] for item in scores) / len(scores), 4) if scores else 0.0,
        "evidence_coverage": round(sum(item["evidence_coverage"] for item in scores) / len(scores), 4) if scores else 0.0,
        "json_validity": round(sum(record.get("status") == "success" and isinstance(record.get("prediction"), dict) for record in predictions.get("records", [])) / max(1, len(predictions.get("records", []))), 4),
        "note": "训练标签仅用于离线评估；测试预测流程不会加载标签目录。",
    }
    output = Path(cache_dir) / "evaluation.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(metric, ensure_ascii=False, indent=2), encoding="utf-8")
    return metric
