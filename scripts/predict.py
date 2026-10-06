from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from city_agent.local_model import ModelConfig
from city_agent.pipeline import predict_reports
from city_agent.adapter import validate_predictions_v1


def main() -> int:
    parser = argparse.ArgumentParser(description="生成初赛预测结果")
    parser.add_argument("--source", required=True, help="官方 ZIP 或已解压目录")
    parser.add_argument("--result-dir", default="result")
    parser.add_argument("--cache-dir", default="cache")
    parser.add_argument("--model-path", default=None)
    parser.add_argument("--split", choices=("test", "train"), default="test")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--resume", action="store_true", help="只重算未成功或缺少 DOCX 的记录")
    args = parser.parse_args()
    config = ModelConfig.from_env()
    if args.model_path:
        config.model_path = args.model_path
    records = predict_reports(
        args.source,
        args.result_dir,
        args.cache_dir,
        model_config=config,
        split=args.split,
        limit=args.limit or None,
        resume=args.resume,
    )
    prediction_path = Path(args.result_dir) / "predictions.v1.json"
    errors = validate_predictions_v1(prediction_path)
    summary = {
        "total": len(records),
        "success": sum(item.status == "success" for item in records),
        "blocked": sum(item.status == "blocked" for item in records),
        "failed": sum(item.status == "failed" for item in records),
        "predictions": str(prediction_path.resolve()),
        "validation_errors": errors,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    incomplete = any(item.status != "success" for item in records)
    if incomplete:
        summary["success_requirement"] = "未满足：存在 blocked/failed 记录"
    return 1 if errors or incomplete else 0


if __name__ == "__main__":
    raise SystemExit(main())
