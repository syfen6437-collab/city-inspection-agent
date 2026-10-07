from __future__ import annotations

import json
import re
import unicodedata
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

from .document_parser import ParsedReport, parse_document_bytes
from .schema import ReportPrediction
from .training_data import normalized_stem, pair_training_files, training_inventory


SUMMARY_FIELDS = {
    "桥梁名称": "bridge_name", "报告编号": "report_number",
    "检测日期": "inspection_date", "报告日期": "inspection_date",
    "总体技术状况评分": "overall_score", "总体评分": "overall_score",
    "总体技术状况等级": "overall_grade", "总体等级": "overall_grade",
    "上部结构评分": "component_scores.superstructure",
    "上部结构等级": "component_scores.superstructure_grade",
    "下部结构评分": "component_scores.substructure",
    "下部结构等级": "component_scores.substructure_grade",
    "桥面系评分": "component_scores.bridge_deck_system",
    "桥面系等级": "component_scores.bridge_deck_system_grade",
    "上一次总体评分": "previous_overall_score", "上一次总体等级": "previous_overall_grade",
    "病害发展趋势与具体说明": "disease_trend", "总体结论": "summary",
    "主要风险点": "key_risks", "建议": "recommendation_counts",
}
LABEL_PARSER_VERSION = "labels.v3"


def _normalize_text(value: Any) -> str:
    if isinstance(value, list):
        value = "；".join(str(item) for item in value)
    if isinstance(value, bool):
        value = "是" if value else "否"
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value if value is not None else ""))).strip()


def _cells(block: Any) -> list[str]:
    return block.cells if getattr(block, "cells", None) is not None else [item.strip() for item in block.text.split("|")]


def parse_label(report: ParsedReport) -> dict[str, Any]:
    result: dict[str, Any] = {"summary": {}, "diseases": [], "recommendations": [], "warnings": []}
    section = None
    sections = {"（1）详细结论": "detailed_conclusion", "病害成因：": "disease_causes", "处置建议：": "disposal_recommendations", "安全影响：": "safety_impacts"}
    normalized_sections = {_normalize_text(key): value for key, value in sections.items()}
    for block in report.blocks:
        if block.kind != "paragraph":
            continue
        text = _normalize_text(block.text)
        if text in normalized_sections:
            section = normalized_sections[text]
        elif text in {_normalize_text("（2）建议明细"), "病害列表", "2、详细信息", "1、简要信息"}:
            section = None
        elif section:
            result.setdefault(section, []).append(block.text)
    tables: dict[int, list[Any]] = {}
    for block in report.blocks:
        if block.kind == "table" and block.table_index is not None:
            tables.setdefault(block.table_index, []).append(block)
    for blocks in tables.values():
        blocks.sort(key=lambda block: block.row_index if block.row_index is not None else -1)
        header = [_normalize_text(item) for item in _cells(blocks[0])]
        if "字段" in header or (header and header[0] in SUMMARY_FIELDS):
            for block in blocks[1:] if "字段" in header else blocks:
                cells = _cells(block)
                if len(cells) >= 2 and _normalize_text(cells[0]) in SUMMARY_FIELDS:
                    result["summary"][_normalize_text(cells[0])] = cells[1]
            continue
        if "建议类别" in header and "建议内容" in header:
            mapping = {"建议类别": "category", "建议内容": "content", "病害部位": "location"}
            target = "recommendations"
        elif "病害类型" in header and ("病害部位" in header or "病害位置" in header):
            mapping = {"病害部位": "location", "病害位置": "location", "病害类型": "disease_type", "病害描述": "description", "描述": "description", "是否新增": "is_new", "上一次定检状态": "previous_status", "发展程度": "development"}
            target = "diseases"
        else:
            continue
        for block in blocks[1:]:
            cells = _cells(block)
            if not any(cells) or cells == _cells(blocks[0]):
                continue
            if len(cells) != len(header):
                result["warnings"].append(f"{block.source_id}: column count mismatch")
                continue
            result[target].append({field: cells[index] for index, name in enumerate(header) if (field := mapping.get(name))})
    if not result["summary"]:
        result["warnings"].append("No recognized summary table")
    return result


def match_label_filename(file_name: str) -> str:
    return normalized_stem(file_name)


def _prediction_field(prediction: dict[str, Any], path: str) -> Any:
    if path == "recommendation_counts":
        counts = Counter(item.get("category") for item in prediction.get("recommendation_details") or [])
        return "、".join(f"{counts[category]}条{category}" for category in ("立即处置", "尽快维修", "预防性养护"))
    value: Any = prediction
    for key in path.split("."):
        value = value.get(key) if isinstance(value, dict) else None
    return value


def label_consistency_issues(label: dict[str, Any]) -> list[dict[str, Any]]:
    """Flag contradictions within a label; do not rewrite reference answers."""
    summary = label.get("summary") or {}
    detail = "\n".join(label.get("detailed_conclusion") or [])
    issues = []
    patterns = {
        "总体评分": r"总体技术状况评分\s*(\d+(?:\.\d+)?)",
        "总体等级": r"总体技术状况等级(?:为)?\s*([A-D]级)",
        "上部结构评分": r"上部结构评分\s*(\d+(?:\.\d+)?)",
        "下部结构评分": r"下部结构评分\s*(\d+(?:\.\d+)?)",
        "桥面系评分": r"桥面系评分\s*(\d+(?:\.\d+)?)",
        "上一次总体评分": r"与上(?:一年度|一次)[^。\n]{0,50}?总体评分\s*(\d+(?:\.\d+)?)",
    }
    for field, pattern in patterns.items():
        match = re.search(pattern, detail)
        if match and summary.get(field) and not _field_equal(summary[field], match.group(1), SUMMARY_FIELDS[field]):
            issues.append({"field": field, "summary_value": summary[field], "detail_value": match.group(1), "reason": "summary_detail_conflict"})
    if label.get("recommendations") and summary.get("建议"):
        counts = _prediction_field({"recommendation_details": label["recommendations"]}, "recommendation_counts")
        if not _field_equal(summary["建议"], counts, "recommendation_counts"):
            issues.append({"field": "建议", "summary_value": summary["建议"], "detail_value": counts, "reason": "summary_row_counts_conflict"})
    return issues


def _field_equal(label: Any, value: Any, path: str) -> bool:
    left, right = _normalize_text(label), _normalize_text(value)
    if not left or not right:
        return left == right
    if path.endswith("score") or path.endswith(("superstructure", "substructure", "bridge_deck_system")):
        try:
            return abs(float(left) - float(right)) <= 0.005
        except ValueError:
            pass
    if path.endswith("grade"):
        return left.upper().removesuffix("级") == right.upper().removesuffix("级")
    if path == "inspection_date":
        def date(text: str) -> tuple[int, ...] | str:
            match = re.fullmatch(r"(20\d{2})[年/-](\d{1,2})(?:月|[-/](\d{1,2})|月(\d{1,2})日)?", text)
            return tuple(int(item) for item in match.groups() if item is not None) if match else text
        return date(left) == date(right)
    if path == "recommendation_counts":
        def counts(text: str) -> dict[str, int]:
            return {category: int(count) for count, category in re.findall(r"(\d+)条(立即处置|尽快维修|预防性养护)", text)}
        return bool(counts(left)) and all(counts(right).get(category, 0) == count for category, count in counts(left).items()) and all(counts(left).get(category, 0) == count for category, count in counts(right).items())
    return left == right


def _text_f1(label: Any, value: Any) -> float:
    left, right = Counter(_normalize_text(label)), Counter(_normalize_text(value))
    total = sum(left.values()) + sum(right.values())
    return 2 * sum((left & right).values()) / total if total else 1.0


def _row_recall(labels: list[dict[str, Any]], predictions: list[dict[str, Any]], fields: tuple[str, ...]) -> float:
    def signature(item: dict[str, Any]) -> tuple[str, ...]:
        return tuple(_normalize_text(item.get(field)) for field in fields)
    expected = Counter(signature(item) for item in labels)
    actual = Counter(signature(item) for item in predictions)
    return sum((expected & actual).values()) / len(labels) if labels else float(not predictions)


def _field_match(prediction: dict[str, Any], labels: dict[str, Any], report: ParsedReport | None = None) -> dict[str, Any]:
    summary = prediction.get("prediction") or {}
    if prediction.get("status") and prediction["status"] != "success":
        summary = {}
    label_summary = labels.get("summary") or {}
    comparisons = {}
    similarities = []
    for key, value in label_summary.items():
        path = SUMMARY_FIELDS.get(key)
        if path is None:
            continue
        predicted = _prediction_field(summary, path)
        comparisons[key] = _field_equal(value, predicted, path)
        similarities.append(_text_f1(value, predicted))
    detail_fields = ("detailed_conclusion", "disease_causes", "disposal_recommendations", "safety_impacts")
    detail_similarities = [_text_f1(labels[key], summary.get(key)) for key in detail_fields if labels.get(key)]
    predicted_diseases = summary.get("diseases") or []
    label_diseases = labels.get("diseases") or []
    sources = {block.source_id: block.text for block in report.blocks} if report else None
    evidence_ids = set()
    invalid = 0
    for item in summary.get("evidence") or []:
        source_id, quote = item.get("source_id"), item.get("quote")
        if source_id and quote and (sources is None or (source_id in sources and quote in sources[source_id])):
            evidence_ids.add(source_id)
        else:
            invalid += 1
    coverage = sum(bool(set(item.get("evidence_ids") or []) & evidence_ids) for item in predicted_diseases)
    return {
        "summary_field_accuracy": sum(comparisons.values()) / len(comparisons) if comparisons else 0.0,
        "summary_character_f1": sum(similarities) / len(similarities) if similarities else 0.0,
        "detailed_character_f1": sum(detail_similarities) / len(detail_similarities) if detail_similarities else 0.0,
        "fields": comparisons,
        "disease_record_recall": _row_recall(label_diseases, predicted_diseases, ("location", "disease_type", "description")),
        "disease_status_recall": _row_recall(label_diseases, predicted_diseases, ("location", "disease_type", "description", "is_new", "previous_status", "development")),
        "recommendation_recall": _row_recall(labels.get("recommendations") or [], summary.get("recommendation_details") or [], ("category", "content", "location")),
        "evidence_coverage": coverage / len(predicted_diseases) if predicted_diseases else 0.0,
        "invalid_evidence_count": invalid,
        "evidence_source_checked": sources is not None,
    }


def evaluate_predictions(source: str | Path, predictions_path: str | Path, cache_dir: str | Path, limit: int | None = None) -> dict[str, Any]:
    payload = json.loads(Path(predictions_path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise ValueError("Predictions must contain a records array")
    if payload.get("split") != "train":
        raise ValueError("Offline label evaluation requires split=train; test predictions must not be scored against training labels")
    if Path(source).suffix.lower() != ".zip":
        raise ValueError("Evaluation requires the official ZIP to retain year and file identity")
    originals, labels = training_inventory(source)
    pairing = pair_training_files(originals, labels)
    by_path = {pair["train_file"]: pair for pair in pairing["pairs"]}
    by_name: dict[str, list[dict[str, Any]]] = {}
    for pair in pairing["pairs"]:
        by_name.setdefault(Path(pair["train_file"]).name, []).append(pair)
    records = payload.get("records", [])[:limit or None]
    scores, failures = [], []
    valid_count = 0
    seen = set()
    with zipfile.ZipFile(source) as handle:
        for record in records:
            name = record.get("file_name", "")
            candidates = [by_path[name]] if name in by_path else by_name.get(Path(name).name, [])
            if len(candidates) != 1:
                failures.append({"file_name": name, "error": "missing/ambiguous pair or duplicate prediction"})
                continue
            pair = candidates[0]
            if pair["train_file"] in seen:
                failures.append({"file_name": name, "error": "duplicate prediction for the same original"})
                continue
            seen.add(pair["train_file"])
            try:
                label = parse_label(parse_document_bytes(handle.read(pair["label_file"]), pair["label_file"]))
                report = parse_document_bytes(handle.read(pair["train_file"]), pair["train_file"])
                if label["warnings"]:
                    raise ValueError("; ".join(label["warnings"]))
                if record.get("status") == "success":
                    ReportPrediction.from_dict(record.get("prediction"))
                    valid_count += 1
                scores.append({"file_name": name, **_field_match(record, label, report)})
            except Exception as exc:
                failures.append({"file_name": name, "error": str(exc)})
    metrics = ("summary_field_accuracy", "summary_character_f1", "detailed_character_f1", "disease_record_recall", "disease_status_recall", "recommendation_recall", "evidence_coverage")
    metric = {key: round(sum(item[key] for item in scores) / len(scores), 6) if scores else None for key in metrics}
    metric.update({"train_reports": len(originals), "label_reports": len(labels), "paired": pairing["paired_count"], "prediction_records": len(records), "matched": len(scores), "evaluation_failures": len(failures), "complete": bool(records) and not failures, "evaluation_coverage": len(scores) / len(records) if records else 0.0, "json_validity": valid_count / len(records) if records else 0.0, "invalid_evidence_count": sum(item["invalid_evidence_count"] for item in scores), "records": scores, "failures": failures, "note": "Offline diagnostic metrics, not the official scoring formula. JSON/schema validity does not imply a nonempty or correct answer. Partial evaluations are incomplete."})
    output = Path(cache_dir) / "evaluation.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(metric, ensure_ascii=False, indent=2), encoding="utf-8")
    return metric
