from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from city_agent.packaging import package_submission


def main() -> int:
    parser = argparse.ArgumentParser(description="生成赛事提交包")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--output", default="city_inspection_agent.tar.gz")
    parser.add_argument("--result-dir", default="result", help="要打包的结果目录")
    args = parser.parse_args()
    target = package_submission(args.project_root, args.output, args.result_dir)
    print(json.dumps({"path": str(target.resolve()), "bytes": target.stat().st_size}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
