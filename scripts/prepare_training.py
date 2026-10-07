from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from city_agent.document_parser import parse_document_bytes
from city_agent.evaluation import LABEL_PARSER_VERSION, label_consistency_issues, parse_label
from city_agent.pipeline import _atomic_json
from city_agent.training_data import build_group_split, write_inventory


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit training pairs and create bridge-separated holdout")
    parser.add_argument("--source", required=True)
    parser.add_argument("--cache-dir", default=str(ROOT / "cache" / "training"))
    parser.add_argument("--parse-labels", action="store_true", help="Parse all labels locally; .doc requires Word")
    args = parser.parse_args()
    cache = Path(args.cache_dir)
    inventory = write_inventory(args.source, cache)
    failures, label_rows, label_names = [], [], {}
    if args.parse_labels:
        prior_path = cache / "parsed_labels.json"
        prior = json.loads(prior_path.read_text(encoding="utf-8")) if prior_path.exists() else {}
        reusable = {item["label_file"]: item for item in prior.get("records", [])} if prior.get("parser_version") == LABEL_PARSER_VERSION else {}
        with zipfile.ZipFile(args.source) as handle:
            for index, pair in enumerate(inventory["pairs"], 1):
                try:
                    info = handle.getinfo(pair["label_file"])
                    old = reusable.get(pair["label_file"])
                    if old and old.get("source_crc32") == info.CRC:
                        label = old["label"]
                    else:
                        report = parse_document_bytes(handle.read(pair["label_file"]), pair["label_file"])
                        label = parse_label(report)
                    if label["warnings"]:
                        raise ValueError("; ".join(label["warnings"]))
                    label_names[pair["label_file"]] = label["summary"].get("桥梁名称", "")
                    label_rows.append({"train_file": pair["train_file"], "label_file": pair["label_file"], "source_crc32": info.CRC, "label": label})
                except Exception as exc:
                    failures.append({"label_file": pair["label_file"], "error": str(exc)})
                if index % 25 == 0 or index == len(inventory["pairs"]):
                    print(f"Labels {index}/{len(inventory['pairs'])}; parsed={len(label_rows)} failed={len(failures)}", flush=True)
                    _atomic_json(cache / "parsed_labels.json", {"split": "labels", "parser_version": LABEL_PARSER_VERSION, "records": label_rows, "failures": failures})
    split = build_group_split(inventory["pairs"], label_names)
    _atomic_json(cache / "train_validation_split.json", split)
    conflicts = [{"label_file": item["label_file"], "issues": issues} for item in label_rows if (issues := label_consistency_issues(item["label"]))]
    _atomic_json(cache / "label_consistency_audit.json", {"records_checked": len(label_rows), "reports_with_conflicts": len(conflicts), "records": conflicts, "note": "Potential internal label contradictions only; originals and labels remain unchanged."})
    summary = {
        "train_count": inventory["train_count"], "label_count": inventory["label_count"],
        "paired_count": inventory["paired_count"], "unmatched_count": len(inventory["unmatched"]),
        "unused_labels": len(inventory["unused_labels"]), "parsed_labels": len(label_rows),
        "label_parse_failures": len(failures), "group_count": split["group_count"],
        "label_consistency_flags": len(conflicts),
        "training_reports": split["train_count"], "validation_reports": split["validation_count"],
        "labels_used_for_grouping": args.parse_labels,
        "note": "Offline preparation only. No inference, cloud upload, or official score claimed.",
    }
    _atomic_json(cache / "preparation_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if inventory["unmatched"] or inventory["unused_labels"] or failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
