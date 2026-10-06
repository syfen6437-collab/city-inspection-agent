from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from .schema import PredictionRecord, RunManifest


PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
ID_RE = re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)")
PERSON_LABEL_RE = re.compile(
    r"((?:联系人|责任人|负责人|检测人员|检查人员|报告编制人|编制人|复核人|审核人|批准人|签字人|姓名)\s*[:：]\s*)[\u4e00-\u9fff]{2,4}"
)
ADDRESS_LABEL_RE = re.compile(
    r"((?:联系地址|通讯地址|收件地址|家庭住址|现住址|住址)\s*[:：]\s*)[^，,；;\r\n。]{3,80}"
)


def redact_text(value: str) -> str:
    value = PHONE_RE.sub("[电话已脱敏]", value)
    value = EMAIL_RE.sub("[邮箱已脱敏]", value)
    value = ID_RE.sub("[证件号已脱敏]", value)
    value = PERSON_LABEL_RE.sub(r"\1[姓名已脱敏]", value)
    return ADDRESS_LABEL_RE.sub(r"\1[地址已脱敏]", value)


def redact_public(value: Any, key: str = "") -> Any:
    if isinstance(value, str):
        if any(
            token in key.lower()
            for token in (
                "phone", "mobile", "email", "address", "contact", "reviewer", "approver",
                "联系人", "责任人", "负责人", "检测人员", "检查人员", "报告编制人", "编制人",
                "复核人", "审核人", "批准人", "签字人", "姓名", "电话", "手机", "邮箱", "地址",
            )
        ):
            return "[已脱敏]"
        return redact_text(value)
    if isinstance(value, list):
        return [redact_public(item, key) for item in value]
    if isinstance(value, dict):
        return {item_key: redact_public(item_value, item_key) for item_key, item_value in value.items()}
    return value


def write_predictions_v1(records: list[PredictionRecord], path: str | Path, input_source: str, split: str, model: dict[str, Any]) -> RunManifest:
    manifest = RunManifest(
        input_source=input_source,
        split=split,
        total=len(records),
        success=sum(record.status == "success" for record in records),
        blocked=sum(record.status == "blocked" for record in records),
        failed=sum(record.status == "failed" for record in records),
        records=records,
        model=model,
    )
    payload = redact_public(manifest.to_dict())
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return manifest


def validate_predictions_v1(path: str | Path) -> list[str]:
    errors: list[str] = []
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") != "predictions.v1":
        errors.append("schema_version 必须为 predictions.v1")
    records = payload.get("records")
    if not isinstance(records, list):
        return errors + ["records 必须是数组"]
    for index, record in enumerate(records):
        if not record.get("file_name") or not record.get("report_id"):
            errors.append(f"records[{index}] 缺少 file_name/report_id")
        if record.get("status") == "success" and not isinstance(record.get("prediction"), dict):
            errors.append(f"records[{index}] success 记录必须有 prediction")
        if record.get("status") != "success" and not record.get("error"):
            errors.append(f"records[{index}] 非 success 记录必须有 error")
    return errors
