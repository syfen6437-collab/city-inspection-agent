from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .document_parser import ParsedReport
from .local_model import LocalModel
from .schema import DiseaseRecord, EvidenceSpan, PredictionRecord, ReportPrediction


SUMMARY_SYSTEM = """只依据报告片段抽取事实。只输出一个紧凑 JSON 对象，不要 Markdown、解释或多余字段。
固定字段：bridge_name, report_number, inspection_date, inspection_year, overall_score, overall_grade, component_scores。
未知字段用 null 或 {}。字符串保持简短，不输出个人信息。不要输出换行或长解释。"""

DETAIL_SYSTEM = """只依据报告片段抽取事实。只输出一个紧凑 JSON 对象，不要 Markdown、解释或多余字段。
固定字段：diseases, recommendations, evidence。
diseases 只抽取输入片段中明确出现的病害行，不要合并或臆测；每项固定字段 location,disease_type,description,is_new,previous_status,development,measurement,evidence_ids；is_new 只能是 true、false 或 null。
recommendations 逐条保留原文，不要改写。evidence 每项只输出 source_id 和 quote，quote 必须是输入片段的连续原文。
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
    # Source-table extraction may produce valid evidence ids without asking
    # the model to repeat the same quote. Materialize the quote locally.
    known_evidence = {item.source_id for item in value.evidence}
    for disease in value.diseases:
        for source_id in disease.evidence_ids:
            if source_id in known_evidence:
                continue
            source_text = valid_sources.get(source_id)
            if source_text:
                value.evidence.append(EvidenceSpan(source_id, source_text[:1200], "table"))
                known_evidence.add(source_id)
    value.key_risks = list(dict.fromkeys(value.key_risks))
    value.recommendations = list(dict.fromkeys(value.recommendations))
    value.standards = list(dict.fromkeys(value.standards))
    return value


def _cells(block: Any) -> list[str]:
    """Split flattened Word table text and remove merge-cell duplicates."""
    raw = [part.strip() for part in str(block.text).split("|")]
    result: list[str] = []
    for part in raw:
        if part and (not result or part != result[-1]):
            result.append(part)
    return result


def _numbered_items(text: str) -> list[str]:
    text = str(text or "").strip()
    if not text:
        return []
    found = re.findall(r"(?:^|[\r\n；;])\s*[（(]?\d+[）).、.]\s*([^\r\n；;]+)", text)
    return [item.strip() for item in found if item.strip()]


def extract_structured_tables(report: ParsedReport) -> tuple[list[DiseaseRecord], list[str]]:
    """Extract explicit disease/recommendation rows from the report's own tables.

    This is a schema-aware document parser, not an answer table: every value is
    copied from a source row and each record retains its source block id.
    """
    diseases: list[DiseaseRecord] = []
    recommendations: list[str] = []
    by_table: dict[int, list[Any]] = {}
    for block in report.blocks:
        if block.kind == "table" and block.table_index is not None:
            by_table.setdefault(block.table_index, []).append(block)
    for table_index, blocks in by_table.items():
        blocks.sort(key=lambda b: (b.row_index if b.row_index is not None else -1))
        header = " ".join(_cells(blocks[0])) if blocks else ""
        normalized = re.sub(r"\s+", "", header)
        is_disease = "病害位置" in normalized and "病害类型" in normalized
        is_recommendation = "建议类别" in normalized and "建议内容" in normalized
        if not (is_disease or is_recommendation):
            continue
        previous_location: str | None = None
        previous_type: str | None = None
        for block in blocks[1:]:
            cells = _cells(block)
            if is_recommendation:
                if len(cells) >= 3 and cells[0] not in {"序号", ""}:
                    recommendations.append(cells[2])
                continue
            if not cells or cells[0].startswith(("1）病害", "2）照片")):
                continue
            # Typical rows are 序号|位置|类型|描述|是否新增|前次状态|发展程度.
            # Word drops empty merged cells while flattening, so continuation
            # rows are recognized by their status column instead of position.
            if len(cells) < 1:
                continue
            if len(cells) >= 4 and cells[1] in {"是", "否", "新增", "无"}:
                location, disease_type, description = previous_location, previous_type, cells[0]
                status_cells = cells[1:4]
            elif len(cells) >= 5 and cells[2] in {"是", "否", "新增", "无"}:
                location, disease_type, description = previous_location, cells[0], cells[1]
                status_cells = cells[2:5]
            elif len(cells) >= 4 and cells[0].isdigit():
                location, disease_type, description = cells[1], cells[2], cells[3]
                status_cells = cells[4:7]
            elif len(cells) >= 3:
                location, disease_type, description = cells[0], cells[1], cells[2]
                status_cells = cells[3:6]
            else:
                continue
            if location:
                previous_location = location
            if disease_type:
                previous_type = disease_type
            is_new: bool | None = None
            previous_status: str | None = None
            development: str | None = None
            if len(status_cells) >= 3:
                value = status_cells[0]
                is_new = True if value in {"是", "新增", "有"} else False if value in {"否", "非新增", "无"} else None
                previous_status, development = status_cells[1], status_cells[2]
            evidence_id = block.source_id
            if location or disease_type or description:
                diseases.append(DiseaseRecord(
                    location=location or None,
                    disease_type=disease_type or None,
                    description=description or None,
                    is_new=is_new,
                    previous_status=previous_status,
                    development=development,
                    measurement=None,
                    evidence_ids=[evidence_id],
                ))
    # Keep order while removing exact duplicate rows caused by merged cells.
    unique: list[DiseaseRecord] = []
    seen: set[tuple[Any, ...]] = set()
    for item in diseases:
        key = (item.location, item.disease_type, item.description, item.is_new, item.previous_status, item.development)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique, list(dict.fromkeys(recommendations))


def extract_source_recommendations(report: ParsedReport) -> list[str]:
    items: list[str] = []
    blocks = list(report.blocks)
    start = next((i for i, b in enumerate(blocks) if "处置建议" in b.text), None)
    if start is None:
        start = next((i for i, b in enumerate(blocks) if "建议如下" in b.text or "维修建议" in b.text), None)
    if start is None:
        return items
    collected = False
    for offset, block in enumerate(blocks[start:]):
        text = block.text.strip()
        found = _numbered_items(text)
        if offset and collected and not found:
            break
        if found:
            collected = True
            items.extend(found)
    return list(dict.fromkeys(items))


def extract_source_facts(report: ParsedReport) -> dict[str, Any]:
    """Find high-confidence facts stated verbatim in report text."""
    # Prefer paragraph text for metadata: Word repeats merged table cells many
    # times when they are flattened into source blocks.
    text = "\n".join(block.text for block in report.blocks if block.kind == "paragraph")
    full_text = "\n".join(block.text for block in report.blocks)
    facts: dict[str, Any] = {}
    stem = Path(report.file_name).stem
    stem = re.sub(r"^[^-]+-", "", stem)
    stem = re.sub(r"[（(][^）)]*(?:原|现|级)[^）)]*[）)]", "", stem)
    stem = re.sub(r"报告(?:\s*\d*)?$", "", stem).strip()
    if stem and len(stem) >= 2 and stem.lower() not in {"sample", "test", "report"}:
        facts["bridge_name"] = stem
    for pattern, key in [
        (r"(?:工程名称|桥梁名称)\s*[:：]\s*([^\n|]+)", "bridge_name"),
        (r"项目名称\s*[:：]\s*([^\n|]+)", "bridge_name"),
        (r"报告编号\s*[:：]\s*([A-Za-z0-9-]+)", "report_number"),
        (r"(?:检测日期|检验日期|报告日期)\s*[:：]\s*([^\n|]+)", "inspection_date"),
    ]:
        match = re.search(pattern, text)
        if match:
            candidate = match.group(1).strip()
            if key != "bridge_name" or ("bridge_name" not in facts and not any(token in candidate for token in ("年度", "定期检测", "结构设施"))):
                facts[key] = candidate
    score = re.search(r"(?:BCI|总体(?:技术状况)?评分|总体评分)\s*[=:：]?\s*([0-9]+(?:\.[0-9]+)?)", full_text, re.I)
    if score:
        facts["overall_score"] = float(score.group(1))
    grade = re.search(r"(?:总体(?:技术状况)?等级|总体等级|整体技术状况(?:等级)?评定为|技术状况等级评定为)\s*(?:为|：|:)??\s*([A-DＡ-Ｄ])\s*级?", full_text, re.I)
    if grade:
        facts["overall_grade"] = grade.group(1).replace("级", "")
    component_scores: dict[str, Any] = {}
    for pattern, key in [
        (r"上部结构评分\s*([0-9]+(?:\.[0-9]+)?)", "superstructure"),
        (r"下部结构评分\s*([0-9]+(?:\.[0-9]+)?)", "substructure"),
        (r"桥面系评分\s*([0-9]+(?:\.[0-9]+)?)", "bridge_deck_system"),
    ]:
        match = re.search(pattern, full_text)
        if match:
            component_scores[key] = float(match.group(1))
    if component_scores:
        facts["component_scores"] = component_scores
    conclusion_blocks = [
        block.text for block in report.blocks
        if any(token in block.text for token in ("检测结论", "总体结论", "经综合评定"))
    ]
    conclusion_blocks = [item for item in conclusion_blocks if len(item.replace(" | ", "")) > 40]
    if conclusion_blocks:
        value = conclusion_blocks[0]
        if " | " in value:
            parts = value.split(" | ")
            value = parts[1] if parts[0] in {"检测结论", "总体结论"} and len(parts) > 1 else parts[0]
        facts["summary"] = value
    date = facts.get("inspection_date", "")
    year = re.search(r"(20\d{2})", str(date))
    if year:
        facts["inspection_year"] = int(year.group(1))
    return facts


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
        # Explicit source facts have priority over a model paraphrase.
        merged = {**summary_data, **detail_data, **extract_source_facts(report)}
        parsed_diseases, parsed_recommendations = extract_structured_tables(report)
        # Prefer complete source tables. The model remains responsible for
        # summaries and for reports without a machine-readable disease table.
        if parsed_diseases:
            model_diseases = detail_data.get("diseases") if isinstance(detail_data.get("diseases"), list) else []
            for row in parsed_diseases:
                for candidate in model_diseases:
                    if not isinstance(candidate, dict):
                        continue
                    overlap = sum(
                        bool(a and b and (str(a) in str(b) or str(b) in str(a)))
                        for a, b in ((row.location, candidate.get("location")), (row.disease_type, candidate.get("disease_type")))
                    )
                    if overlap >= 1:
                        row.is_new = candidate.get("is_new") if row.is_new is None else row.is_new
                        row.previous_status = candidate.get("previous_status") if row.previous_status is None else row.previous_status
                        row.development = candidate.get("development") if row.development is None else row.development
                        row.measurement = candidate.get("measurement") if row.measurement is None else row.measurement
                        break
            merged["diseases"] = [item.to_dict() for item in parsed_diseases]
        source_recommendations = extract_source_recommendations(report)
        if source_recommendations:
            merged["recommendations"] = source_recommendations
        elif parsed_recommendations:
            merged["recommendations"] = parsed_recommendations
        prediction = _clean_prediction(ReportPrediction.from_dict(merged), report)
        metadata = {**summary_metadata, "detail_latency_ms": detail_metadata.get("latency_ms")}
        return PredictionRecord(report.file_name, report.report_id, "success", prediction, model=metadata)
    except Exception as exc:
        return PredictionRecord(report.file_name, report.report_id, "blocked", None, str(exc), model=model.metadata)
