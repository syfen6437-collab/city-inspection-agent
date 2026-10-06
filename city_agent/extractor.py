from __future__ import annotations

import json
from typing import Any

from .document_parser import ParsedReport
from .local_model import LocalModel
from .schema import EvidenceSpan, PredictionRecord, ReportPrediction


SUMMARY_SYSTEM = """只依据报告片段抽取事实。只输出一个紧凑 JSON 对象，不要 Markdown、解释或多余字段。
固定字段：bridge_name, report_number, inspection_date, inspection_year, overall_score, overall_grade, component_scores。
未知字段用 null 或 {}。字符串保持简短，不输出个人信息。不要输出换行或长解释。"""

DETAIL_SYSTEM = """只依据报告片段抽取事实。只输出一个紧凑 JSON 对象，不要 Markdown、解释或多余字段。
固定字段：diseases, recommendations, evidence。
diseases 最多 3 条，每项固定字段 location,disease_type,description,is_new,previous_status,development,measurement,evidence_ids；is_new 只能是 true、false 或 null；每个字符串尽量不超过 40 字。
recommendations 最多 3 条短字符串。evidence 最多 3 条，每项只输出 source_id 和 quote，quote 不超过 50 字。
找不到的信息用 null 或空数组；source_id 必须来自片段；不输出个人信息。"""

SUMMARY_FALLBACK_SYSTEM = """上一次概要抽取没有返回合法 JSON。现在只输出 JSON 对象 {}，不要 Markdown、解释、换行或其它文字。
{} 表示报告概要字段均无法从当前输入可靠确定，程序会按 schema 保留 null。"""

DETAIL_FALLBACK_SYSTEM = """上一次病害抽取没有返回合法 JSON。现在只输出 JSON 对象 {}，不要 Markdown、解释、换行或其它文字。
{} 表示病害、建议和证据均无法从当前输入可靠确定，程序会按 schema 保留空数组。"""


def build_report_context(report: ParsedReport, max_blocks: int = 32, max_chars: int = 5000) -> str:
    preferred = ("病害", "检测结论", "评估结论", "维护建议", "建议", "BCI", "评分", "等级", "桥梁概况")
    ordered = sorted(
        report.blocks,
        key=lambda block: (0 if any(token in block.text for token in preferred) else 1, report.blocks.index(block)),
    )
    selected: list[str] = []
    size = 0
    for block in ordered[:max_blocks]:
        item = f"[{block.source_id}] {block.text}"
        if size + len(item) > max_chars:
            break
        selected.append(item)
        size += len(item) + 1
    return "\n".join(selected)


def _clean_prediction(value: ReportPrediction, report: ParsedReport) -> ReportPrediction:
    valid_sources = {block.source_id: block.text for block in report.blocks}
    cleaned_evidence: list[EvidenceSpan] = []
    seen_sources: set[str] = set()
    for item in value.evidence:
        if item.source_id in valid_sources and item.source_id not in seen_sources:
            seen_sources.add(item.source_id)
            source_text = valid_sources[item.source_id]
            quote = item.quote if item.quote and item.quote in source_text else source_text[:1200]
            cleaned_evidence.append(EvidenceSpan(item.source_id, quote, item.kind, item.table_index, item.row_index, item.page))
    value.evidence = cleaned_evidence
    valid_ids = set(valid_sources)
    seen_diseases: set[tuple[object, ...]] = set()
    unique_diseases = []
    for disease in value.diseases:
        disease.evidence_ids = list(dict.fromkeys(source_id for source_id in disease.evidence_ids if source_id in valid_ids))
        signature = (
            disease.location, disease.disease_type, disease.description, disease.is_new,
            disease.previous_status, disease.development, disease.measurement, tuple(disease.evidence_ids),
        )
        if signature not in seen_diseases:
            seen_diseases.add(signature)
            unique_diseases.append(disease)
    value.diseases = unique_diseases
    value.key_risks = list(dict.fromkeys(value.key_risks))
    value.recommendations = list(dict.fromkeys(value.recommendations))
    value.standards = list(dict.fromkeys(value.standards))
    return value


def _generate(model: LocalModel, system: str, user: str, max_new_tokens: int):
    try:
        return model.generate_json(system, user, max_new_tokens=max_new_tokens)
    except TypeError:
        # Keeps small test doubles and older local model adapters compatible.
        return model.generate_json(system, user)


def extract_report(report: ParsedReport, model: LocalModel) -> PredictionRecord:
    context = build_report_context(report)
    user = json.dumps(
        {
            "file_name": report.file_name,
            "source_metadata_hints": report.source_metadata,
            "report_fragments": context,
        },
        ensure_ascii=False,
    )
    try:
        try:
            summary_data, summary_metadata = _generate(model, SUMMARY_SYSTEM, user, max_new_tokens=256)
        except Exception:
            # Retry through the model with a minimal prompt. An empty object is
            # an explicit unknown response; no answer text is synthesized here.
            summary_data, summary_metadata = _generate(
                model,
                SUMMARY_FALLBACK_SYSTEM,
                json.dumps({"report_file": report.file_name}, ensure_ascii=False),
                max_new_tokens=64,
            )
        try:
            detail_data, detail_metadata = _generate(model, DETAIL_SYSTEM, user, max_new_tokens=512)
        except Exception:
            detail_data, detail_metadata = _generate(
                model,
                DETAIL_FALLBACK_SYSTEM,
                json.dumps({"report_file": report.file_name}, ensure_ascii=False),
                max_new_tokens=64,
            )
        merged = {**summary_data, **detail_data}
        prediction = _clean_prediction(ReportPrediction.from_dict(merged), report)
        metadata = {**summary_metadata, "detail_latency_ms": detail_metadata.get("latency_ms")}
        return PredictionRecord(report.file_name, report.report_id, "success", prediction, model=metadata)
    except Exception as exc:
        return PredictionRecord(report.file_name, report.report_id, "blocked", None, str(exc), model=model.metadata)
