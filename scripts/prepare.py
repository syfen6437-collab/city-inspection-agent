from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from city_agent.pipeline import prepare_corpus


def main() -> int:
    parser = argparse.ArgumentParser(description="解析报告并建立本地检索索引")
    parser.add_argument("--source", required=True, help="官方 ZIP 或已解压目录")
    parser.add_argument("--cache-dir", default="cache")
    parser.add_argument("--split", choices=("test", "train", "labels"), default="test")
    parser.add_argument("--limit", type=int, default=0, help="仅处理前 N 份，0 表示全部")
    args = parser.parse_args()
    reports, index = prepare_corpus(args.source, args.cache_dir, args.split, args.limit or None)
    payload = {
        "source": Path(args.source).name,
        "split": args.split,
        "reports": len(reports),
        "blocks": len(index.blocks),
        "cache_dir": str(Path(args.cache_dir).resolve()),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
