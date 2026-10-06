from __future__ import annotations

import shutil
import tarfile
import tempfile
from pathlib import Path


def package_submission(project_root: str | Path, output_path: str | Path, result_dir: str | Path = "result") -> Path:
    root = Path(project_root)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="city-agent-package-") as temp:
        stage = Path(temp)
        code = stage / "code"
        design = stage / "design"
        result = stage / "result"
        code.mkdir()
        design.mkdir()
        result.mkdir()
        for name in ("city_agent", "scripts", "app.py", "run_predict.ps1", "run_predict.cmd", "requirements.txt", "requirements-model.txt", "README.md", "pyproject.toml"):
            source = root / name
            if source.is_dir():
                shutil.copytree(source, code / source.name, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
            elif source.exists():
                shutil.copy2(source, code / source.name)
        design_source = root / "design"
        if design_source.exists():
            for item in design_source.iterdir():
                if item.is_file() and item.suffix.lower() in {".md", ".docx", ".pdf"}:
                    shutil.copy2(item, design / item.name)
        result_source = Path(result_dir)
        if not result_source.is_absolute():
            result_source = root / result_source
        if result_source.exists():
            for item in result_source.iterdir():
                # The platform matches result documents to input stems. Older
                # local runs used a ``_结果`` suffix; never include those stale
                # documents in a new submission package.
                if item.is_file() and item.name.endswith("_结果.docx"):
                    continue
                if item.is_file() and item.suffix.lower() in {".json", ".docx", ".jsonl"}:
                    shutil.copy2(item, result / item.name)
        with tarfile.open(target, "w:gz") as archive:
            archive.add(stage / "code", arcname="code")
            archive.add(stage / "design", arcname="design")
            archive.add(stage / "result", arcname="result")
    if target.stat().st_size > 1024 * 1024 * 1024:
        raise ValueError("提交包超过 1 GB")
    return target
