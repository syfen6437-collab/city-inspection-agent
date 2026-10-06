from __future__ import annotations

import json
import hashlib
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Iterable, NamedTuple

from .adapter import redact_public, write_predictions_v1, write_standard_predictions
from .document_parser import ParsedReport, parse_document_bytes, parse_document_path
from .extractor import extract_report
from .index import NgramIndex
from .local_model import LocalModel, ModelConfig, ModelUnavailable
from .render import render_prediction_docx
from .schema import PredictionRecord


class ReportInput(NamedTuple):
    file_name: str
    report: ParsedReport | None
    error: str = ""


def _matches_split(name: str, split: str) -> bool:
    normalized = name.replace("\\", "/")
    if split == "test":
        return "/初赛测试集/" in normalized
    if split == "train":
        return "/赛题一_训练集/" in normalized and "/赛题一_训练集标签/" not in normalized
    if split == "labels":
        return "/赛题一_训练集标签/" in normalized
    return True


def iter_archive_documents(archive: str | Path, split: str) -> Iterable[tuple[str, bytes]]:
    with zipfile.ZipFile(archive) as handle:
        for entry in sorted(handle.infolist(), key=lambda item: item.filename):
            if entry.is_dir() or Path(entry.filename).suffix.lower() not in {".docx", ".doc"}:
                continue
            if _matches_split(entry.filename, split):
                yield entry.filename, handle.read(entry)


def load_reports(source: str | Path, split: str = "test", limit: int | None = None) -> list[ParsedReport]:
    path = Path(source)
    reports: list[ParsedReport] = []
    if path.is_file() and path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("解析缓存必须是报告数组")
        from .document_parser import DocumentBlock
        for item in payload:
            reports.append(
                ParsedReport(
                    item["report_id"],
                    item["file_name"],
                    item.get("extension", ".docx"),
                    [DocumentBlock(**block) for block in item.get("blocks", [])],
                    item.get("source_metadata", {}),
                )
            )
            if limit and len(reports) >= limit:
                break
        return reports
    if path.is_file() and path.suffix.lower() == ".zip":
        for name, data in iter_archive_documents(path, split):
            reports.append(parse_document_bytes(data, name))
            if limit and len(reports) >= limit:
                break
        return reports
    paths = sorted(path.rglob("*.docx")) + sorted(path.rglob("*.doc"))
    for item in paths:
        relative = item.as_posix()
        if split in {"test", "train", "labels"} and not _matches_split(relative, split):
            continue
        reports.append(parse_document_path(item))
        if limit and len(reports) >= limit:
            break
    return reports


def load_report_inputs(source: str | Path, split: str = "test", limit: int | None = None) -> list[ReportInput]:
    """Load every selected input while retaining per-file parse failures."""
    path = Path(source)
    inputs: list[ReportInput] = []
    if path.is_file() and path.suffix.lower() == ".json":
        for report in load_reports(path, split=split, limit=limit):
            inputs.append(ReportInput(report.file_name, report))
        return inputs
    if path.is_file() and path.suffix.lower() == ".zip":
        entries: Iterable[tuple[str, bytes]] = iter_archive_documents(path, split)
        for name, data in entries:
            try:
                inputs.append(ReportInput(name, parse_document_bytes(data, name)))
            except Exception as exc:
                inputs.append(ReportInput(name, None, str(exc)))
            if limit and len(inputs) >= limit:
                break
        return inputs
    paths = sorted(path.rglob("*.docx")) + sorted(path.rglob("*.doc"))
    for item in paths:
        relative = item.as_posix()
        if split in {"test", "train", "labels"} and not _matches_split(relative, split):
            continue
        try:
            inputs.append(ReportInput(item.name, parse_document_path(item)))
        except Exception as exc:
            inputs.append(ReportInput(item.name, None, str(exc)))
        if limit and len(inputs) >= limit:
            break
    return inputs


def prepare_corpus(source: str | Path, cache_dir: str | Path, split: str = "test", limit: int | None = None) -> tuple[list[ParsedReport], NgramIndex]:
    reports = load_reports(source, split, limit)
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    (cache / f"reports_{split}.json").write_text(json.dumps([report.to_dict() for report in reports], ensure_ascii=False), encoding="utf-8")
    index = NgramIndex.from_reports(reports)
    index.save(cache / f"index_{split}.json")
    return reports, index


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _atomic_docx(record: PredictionRecord, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".docx", dir=str(path.parent))
    os.close(fd)
    try:
        render_prediction_docx(record, temporary)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _report_id_for_name(file_name: str) -> str:
    return hashlib.sha256(file_name.encode("utf-8")).hexdigest()[:16]


def result_docx_name(file_name: str) -> str:
    """Use the input stem as the official result document identity."""
    return f"{Path(file_name).stem}.docx"


def _load_resume_records(path: Path) -> dict[str, PredictionRecord]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        records: dict[str, PredictionRecord] = {}
        for item in payload.get("records", []):
            try:
                record = PredictionRecord.from_dict(item)
            except (TypeError, ValueError):
                continue
            if record.status == "success" and record.prediction is not None:
                records[record.report_id] = record
        return records
    except Exception:
        return {}


def load_cached_reports(cache_dir: str | Path, split: str) -> list[ParsedReport]:
    payload = json.loads((Path(cache_dir) / f"reports_{split}.json").read_text(encoding="utf-8"))
    from .document_parser import DocumentBlock

    return [
        ParsedReport(
            item["report_id"],
            item["file_name"],
            item["extension"],
            [DocumentBlock(**block) for block in item["blocks"]],
            item.get("source_metadata", {}),
        )
        for item in payload
    ]


def load_cached_index(cache_dir: str | Path, split: str) -> NgramIndex:
    return NgramIndex.load(Path(cache_dir) / f"index_{split}.json")


def predict_reports(
    source: str | Path,
    result_dir: str | Path,
    cache_dir: str | Path,
    model_config: ModelConfig | None = None,
    split: str = "test",
    limit: int | None = None,
    resume: bool = False,
) -> list[PredictionRecord]:
    inputs = load_report_inputs(source, split, limit)
    reports = [item.report for item in inputs if item.report is not None]
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    _atomic_json(cache / f"reports_{split}.json", [report.to_dict() for report in reports])
    NgramIndex.from_reports(reports).save(cache / f"index_{split}.json")
    result = Path(result_dir)
    result.mkdir(parents=True, exist_ok=True)
    model = LocalModel(model_config or ModelConfig.from_env())
    prediction_path = result / "predictions.v1.json"
    resume_records = _load_resume_records(prediction_path) if resume else {}
    records: list[PredictionRecord] = []
    can_reuse_all = bool(inputs) and resume and all(
        item.report is not None
        and resume_records.get(item.report.report_id) is not None
        and (result / result_docx_name(item.file_name)).exists()
        for item in inputs
    )
    if can_reuse_all:
        model_error = ""
    else:
        try:
            model.load()
            model_error = ""
        except ModelUnavailable as exc:
            model_error = str(exc)
    audit = result / "audit.jsonl"
    if not resume and audit.exists():
        audit.unlink()
    for item in inputs:
        report = item.report
        report_id = report.report_id if report is not None else _report_id_for_name(item.file_name)
        existing = resume_records.get(report_id)
        expected_docx = result / result_docx_name(item.file_name)
        if existing is not None and expected_docx.exists():
            record = existing
        elif report is None:
            record = PredictionRecord(item.file_name, report_id, "failed", None, f"文档解析失败：{item.error}", model=model.metadata)
        elif model_error:
            record = PredictionRecord(item.file_name, report_id, "blocked", None, model_error, model=model.metadata)
        else:
            record = extract_report(report, model)
        records.append(record)
        # Re-render reused records so a resumed run picks up renderer and
        # redaction fixes without re-running the local model.
        _atomic_docx(record, expected_docx)
        _atomic_text(
            audit,
            "".join(
                json.dumps(redact_public(audit_record.to_dict()), ensure_ascii=False) + "\n"
                for audit_record in records
            ),
        )
        write_predictions_v1(records, prediction_path, str(source), split, model.metadata)
        write_standard_predictions(records, result / "standard_predictions.json")
    manifest = {
        "source": str(source),
        "split": split,
        "total": len(records),
        "success": sum(record.status == "success" for record in records),
        "blocked": sum(record.status == "blocked" for record in records),
        "failed": sum(record.status == "failed" for record in records),
    }
    _atomic_json(result / "manifest.json", manifest)
    prediction_payload = json.loads(prediction_path.read_text(encoding="utf-8"))
    audit_rows = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines() if line.strip()]
    expected_docx = [result / result_docx_name(item.file_name) for item in inputs]
    actual_docx = [path for path in result.glob("*.docx") if not path.name.endswith("_结果.docx")]
    validation_errors = []
    if len(records) != len(inputs):
        validation_errors.append(f"预测记录数 {len(records)} 与输入数 {len(inputs)} 不一致")
    if len(prediction_payload.get("records", [])) != len(inputs):
        validation_errors.append("predictions.v1.json 条目数与输入数不一致")
    if len(audit_rows) != len(inputs):
        validation_errors.append("audit.jsonl 条目数与输入数不一致")
    if len(actual_docx) != len(inputs) or any(not path.is_file() or not zipfile.is_zipfile(path) for path in expected_docx):
        validation_errors.append("结果 DOCX 数量或文件结构与输入数不一致")
    if validation_errors:
        raise RuntimeError("；".join(validation_errors))
    return records
