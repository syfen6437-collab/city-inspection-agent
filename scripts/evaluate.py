from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from city_agent.evaluation import evaluate_predictions
from city_agent.local_model import ModelConfig
from city_agent.pipeline import predict_reports


def main() -> int:
    parser = argparse.ArgumentParser(description="训练集离线评估")
    parser.add_argument("--source", required=True)
    parser.add_argument("--cache-dir", default="cache")
    parser.add_argument("--result-dir", default="result/train_eval")
    parser.add_argument("--predictions", default=None, help="已有 predictions.v1.json；省略则先推理训练原文")
    parser.add_argument("--model-path", default=None)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    predictions = args.predictions
    if predictions is None:
        config = ModelConfig.from_env()
        if args.model_path:
            config.model_path = args.model_path
        predict_reports(
            args.source,
            args.result_dir,
            args.cache_dir,
            model_config=config,
            split="train",
            limit=args.limit or None,
        )
        predictions = str(Path(args.result_dir) / "predictions.v1.json")
    metric = evaluate_predictions(args.source, predictions, args.cache_dir, args.limit or None)
    print(json.dumps(metric, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
