from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class EvidenceSpan:
    source_id: str
    quote: str
    kind: str = "paragraph"
    table_index: int | None = None
    row_index: int | None = None
    page: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass
class DiseaseRecord:
    location: str | None = None
    disease_type: str | None = None
    description: str | None = None
    is_new: bool | None = None
    previous_status: str | None = None
    development: str | None = None
    measurement: str | None = None
    evidence_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RecommendationRecord:
    category: str | None = None
    content: str | None = None
    location: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReportPrediction:
    bridge_name: str | None = None
    report_number: str | None = None
    inspection_date: str | None = None
    inspection_year: int | None = None
    overall_score: float | None = None
    overall_grade: str | None = None
    component_scores: dict[str, Any] = field(default_factory=dict)
    previous_overall_score: str | None = None
    previous_overall_grade: str | None = None
    disease_trend: str | None = None
    summary: str | None = None
    key_risks: list[str] = field(default_factory=list)
    detailed_conclusion: list[str] = field(default_factory=list)
    disease_causes: list[str] = field(default_factory=list)
    disposal_recommendations: list[str] = field(default_factory=list)
    safety_impacts: list[str] = field(default_factory=list)
    diseases: list[DiseaseRecord] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    recommendation_details: list[RecommendationRecord] = field(default_factory=list)
    standards: list[str] = field(default_factory=list)
    evidence: list[EvidenceSpan] = field(default_factory=list)

    @classmethod
    def from_dict(cls, value: Any) -> "ReportPrediction":
        if not isinstance(value, dict):
            raise ValueError("模型输出必须是 JSON 对象")
        allowed = {
            "bridge_name", "report_number", "inspection_date", "inspection_year",
            "overall_score", "overall_grade", "component_scores", "previous_overall_score",
            "previous_overall_grade", "disease_trend", "summary", "key_risks",
            "detailed_conclusion", "disease_causes", "disposal_recommendations", "safety_impacts",
            "diseases", "recommendations", "recommendation_details",
            "standards", "evidence",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"模型输出包含未声明字段：{sorted(unknown)}")
        for key in (
            "bridge_name", "report_number", "inspection_date", "overall_grade",
            "previous_overall_score", "previous_overall_grade", "disease_trend", "summary",
        ):
            if value.get(key) is not None and not isinstance(value[key], str):
                raise ValueError(f"{key} 必须是字符串或 null")
        diseases = []
        raw_diseases = value.get("diseases")
        if raw_diseases is None:
            raw_diseases = []
        if not isinstance(raw_diseases, list):
            raise ValueError("diseases 必须是数组")
        for item in raw_diseases:
            if not isinstance(item, dict):
                raise ValueError("diseases 每项必须是对象")
            disease_unknown = set(item) - set(DiseaseRecord.__dataclass_fields__)
            if disease_unknown:
                raise ValueError(f"disease 包含未声明字段：{sorted(disease_unknown)}")
            disease = DiseaseRecord(**{k: item.get(k) for k in DiseaseRecord.__dataclass_fields__ if k in item})
            if not isinstance(disease.evidence_ids, list):
                raise ValueError("disease.evidence_ids 必须是数组")
            for key in ("location", "disease_type", "description", "previous_status", "development", "measurement"):
                if getattr(disease, key) is not None and not isinstance(getattr(disease, key), str):
                    raise ValueError(f"disease.{key} 必须是字符串或 null")
            if disease.is_new is not None and not isinstance(disease.is_new, bool):
                raise ValueError("disease.is_new 必须是布尔值或 null")
            if any(not isinstance(source_id, str) for source_id in disease.evidence_ids):
                raise ValueError("disease.evidence_ids 只能包含字符串")
            disease.evidence_ids = [str(x) for x in disease.evidence_ids if x]
            diseases.append(disease)
        recommendation_details = []
        raw_recommendation_details = value.get("recommendation_details")
        if raw_recommendation_details is None:
            raw_recommendation_details = []
        if not isinstance(raw_recommendation_details, list):
            raise ValueError("recommendation_details 必须是数组")
        for item in raw_recommendation_details:
            if not isinstance(item, dict):
                raise ValueError("recommendation_details 每项必须是对象")
            unknown = set(item) - set(RecommendationRecord.__dataclass_fields__)
            if unknown:
                raise ValueError(f"recommendation_details 包含未声明字段：{sorted(unknown)}")
            for key in RecommendationRecord.__dataclass_fields__:
                if item.get(key) is not None and not isinstance(item[key], str):
                    raise ValueError(f"recommendation_details.{key} 必须是字符串或 null")
            recommendation_details.append(RecommendationRecord(**{
                key: item.get(key) for key in RecommendationRecord.__dataclass_fields__
            }))
        evidence = []
        raw_evidence = value.get("evidence")
        if raw_evidence is None:
            raw_evidence = []
        if not isinstance(raw_evidence, list):
            raise ValueError("evidence 必须是数组")
        for item in raw_evidence:
            if not isinstance(item, dict):
                raise ValueError("evidence 每项必须是对象")
            evidence_unknown = set(item) - set(EvidenceSpan.__dataclass_fields__)
            if evidence_unknown:
                raise ValueError(f"evidence 包含未声明字段：{sorted(evidence_unknown)}")
            if not isinstance(item.get("source_id"), str) or not isinstance(item.get("quote"), str):
                raise ValueError("evidence.source_id 和 evidence.quote 必须是字符串")
            if item.get("source_id") and item.get("quote"):
                evidence.append(EvidenceSpan(**{k: item.get(k) for k in EvidenceSpan.__dataclass_fields__ if k in item}))
        score = value.get("overall_score")
        try:
            score = float(score) if score not in (None, "") else None
        except (TypeError, ValueError):
            score = None
        year = value.get("inspection_year")
        try:
            year = int(year) if year not in (None, "") else None
        except (TypeError, ValueError):
            year = None
        return cls(
            bridge_name=value.get("bridge_name"),
            report_number=value.get("report_number"),
            inspection_date=value.get("inspection_date"),
            inspection_year=year,
            overall_score=score,
            overall_grade=value.get("overall_grade"),
            component_scores=value.get("component_scores") if isinstance(value.get("component_scores"), dict) else {},
            previous_overall_score=value.get("previous_overall_score"),
            previous_overall_grade=value.get("previous_overall_grade"),
            disease_trend=value.get("disease_trend"),
            summary=value.get("summary"),
            key_risks=_string_list(value.get("key_risks"), "key_risks"),
            detailed_conclusion=_string_list(value.get("detailed_conclusion"), "detailed_conclusion"),
            disease_causes=_string_list(value.get("disease_causes"), "disease_causes"),
            disposal_recommendations=_string_list(value.get("disposal_recommendations"), "disposal_recommendations"),
            safety_impacts=_string_list(value.get("safety_impacts"), "safety_impacts"),
            diseases=diseases,
            recommendations=_string_list(value.get("recommendations"), "recommendations"),
            recommendation_details=recommendation_details,
            standards=_string_list(value.get("standards"), "standards"),
            evidence=evidence,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "bridge_name": self.bridge_name,
            "report_number": self.report_number,
            "inspection_date": self.inspection_date,
            "inspection_year": self.inspection_year,
            "overall_score": self.overall_score,
            "overall_grade": self.overall_grade,
            "component_scores": self.component_scores,
            "previous_overall_score": self.previous_overall_score,
            "previous_overall_grade": self.previous_overall_grade,
            "disease_trend": self.disease_trend,
            "summary": self.summary,
            "key_risks": self.key_risks,
            "detailed_conclusion": self.detailed_conclusion,
            "disease_causes": self.disease_causes,
            "disposal_recommendations": self.disposal_recommendations,
            "safety_impacts": self.safety_impacts,
            "diseases": [item.to_dict() for item in self.diseases],
            "recommendations": self.recommendations,
            "recommendation_details": [item.to_dict() for item in self.recommendation_details],
            "standards": self.standards,
            "evidence": [item.to_dict() for item in self.evidence],
        }


def _string_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} 必须是字符串数组")
    return value


@dataclass
class PredictionRecord:
    file_name: str
    report_id: str
    status: str
    prediction: ReportPrediction | None = None
    error: str = ""
    model: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    @classmethod
    def from_dict(cls, value: Any) -> "PredictionRecord":
        prediction = value.get("prediction") if isinstance(value, dict) else None
        return cls(
            file_name=str(value.get("file_name", "")),
            report_id=str(value.get("report_id", "")),
            status=str(value.get("status", "failed")),
            prediction=ReportPrediction.from_dict(prediction) if isinstance(prediction, dict) else None,
            error=str(value.get("error", "")),
            model=value.get("model") if isinstance(value.get("model"), dict) else {},
            created_at=str(value.get("created_at", utc_now())),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_name": self.file_name,
            "report_id": self.report_id,
            "status": self.status,
            "prediction": self.prediction.to_dict() if self.prediction else None,
            "error": self.error,
            "model": self.model,
            "created_at": self.created_at,
        }


@dataclass
class RunManifest:
    schema_version: str = "predictions.v1"
    generated_at: str = field(default_factory=utc_now)
    input_source: str = ""
    split: str = "test"
    total: int = 0
    success: int = 0
    blocked: int = 0
    failed: int = 0
    records: list[PredictionRecord] = field(default_factory=list)
    model: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "input_source": self.input_source,
            "split": self.split,
            "total": self.total,
            "success": self.success,
            "blocked": self.blocked,
            "failed": self.failed,
            "model": self.model,
            "records": [record.to_dict() for record in self.records],
        }

    def write(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, ensure_ascii=False, indent=2)
