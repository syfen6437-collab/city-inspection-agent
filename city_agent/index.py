from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from .document_parser import DocumentBlock, ParsedReport
from .schema import EvidenceSpan


def _ngrams(text: str) -> set[str]:
    compact = re.sub(r"\s+", "", text.lower())
    grams: set[str] = set()
    for size in (2, 3, 4):
        grams.update(compact[index : index + size] for index in range(max(0, len(compact) - size + 1)))
    for token in re.findall(r"[a-z0-9_+.-]{2,}", compact):
        grams.add(token)
    return grams


@dataclass
class IndexedBlock:
    report_id: str
    file_name: str
    source_id: str
    text: str
    kind: str
    table_index: int | None = None
    row_index: int | None = None

    def to_dict(self) -> dict:
        return {
            "report_id": self.report_id,
            "file_name": self.file_name,
            "source_id": self.source_id,
            "text": self.text,
            "kind": self.kind,
            "table_index": self.table_index,
            "row_index": self.row_index,
        }


class NgramIndex:
    def __init__(self, blocks: list[IndexedBlock] | None = None):
        self.blocks = blocks or []
        self.postings: dict[str, list[int]] = defaultdict(list)
        self.document_frequency: Counter[str] = Counter()
        if blocks:
            self._build()

    def _build(self) -> None:
        self.postings.clear()
        self.document_frequency.clear()
        for index, block in enumerate(self.blocks):
            grams = _ngrams(block.text)
            for gram in grams:
                self.postings[gram].append(index)
                self.document_frequency[gram] += 1

    @classmethod
    def from_reports(cls, reports: list[ParsedReport]) -> "NgramIndex":
        blocks = []
        for report in reports:
            blocks.extend(
                IndexedBlock(
                    report.report_id,
                    report.file_name,
                    block.source_id,
                    block.text,
                    block.kind,
                    block.table_index,
                    block.row_index,
                )
                for block in report.blocks
            )
        return cls(blocks)

    def search(self, query: str, limit: int = 8, report_id: str | None = None) -> list[EvidenceSpan]:
        grams = _ngrams(query)
        if not grams:
            return []
        scores: Counter[int] = Counter()
        for gram in grams:
            for index in self.postings.get(gram, []):
                block = self.blocks[index]
                if report_id and block.report_id != report_id:
                    continue
                rarity = 1.0 / max(1, self.document_frequency[gram])
                scores[index] += rarity
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]
        return [
            EvidenceSpan(
                source_id=self.blocks[index].source_id,
                quote=self.blocks[index].text[:1200],
                kind=self.blocks[index].kind,
                table_index=self.blocks[index].table_index,
                row_index=self.blocks[index].row_index,
            )
            for index, _ in ranked
        ]

    def context(self, query: str, limit: int = 16, report_id: str | None = None) -> str:
        hits = self.search(query, limit=limit, report_id=report_id)
        return "\n".join(f"[{item.source_id}] {item.quote}" for item in hits)

    def save(self, path: str | Path) -> None:
        payload = {
            "schema_version": "ngram-index.v1",
            "blocks": [block.to_dict() for block in self.blocks],
        }
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "NgramIndex":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if payload.get("schema_version") != "ngram-index.v1":
            raise ValueError("未知索引版本")
        blocks = [IndexedBlock(**item) for item in payload.get("blocks", [])]
        return cls(blocks)

