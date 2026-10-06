from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    parser = argparse.ArgumentParser(description="下载开源 Qwen3-8B 模型到本地缓存")
    parser.add_argument("--model", default="Qwen/Qwen3-8B")
    parser.add_argument("--target", default="models/Qwen3-8B")
    args = parser.parse_args()
    try:
        from modelscope import snapshot_download
    except ImportError as exc:
        raise SystemExit("缺少 modelscope，请安装 requirements-model.txt") from exc
    target = Path(args.target)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        downloaded = snapshot_download(args.model, local_dir=str(target))
    except TypeError:
        # Older ModelScope releases do not accept local_dir; use their cache
        # location and report it so the caller can set CITY_AGENT_MODEL_PATH.
        downloaded = snapshot_download(args.model)
    print(json.dumps({"model": args.model, "path": str(Path(downloaded).resolve())}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
