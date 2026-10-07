"""Offline training inventory only; never imported by test prediction code."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import zipfile
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path, PurePosixPath
from typing import Any


def normalized_stem(name: str) -> str:
    text = unicodedata.normalize("NFKC", PurePosixPath(name.replace("\\", "/")).stem).lower()
    # Some labels contain a second .docx suffix, download-copy suffix, or typo.
    text = re.sub(r"(?:[- ·_]*(?:含有|含|有|无)对比年度).*", "", text)
    text = re.sub(r"[- ·_]*信息提[提]?取(?:报告)?.*", "", text)
    text = re.sub(r"\.docx?$", "", text.strip())
    text = re.sub(r"(?:桥梁)?(?:检测评估|检测|评估|定检)?报告", "", text)
    text = re.sub(r"(?:20\d{2}[.年/-]\d{1,2}[.月/-]\d{1,2}日?|20\d{2}年度)$", "", text)
    text = text.replace("匝道桥", "匝道")
    text = re.sub(r"(k\d+\+\d+)小桥$", r"\1", text)
    return re.sub(r"[\s()【】\[\]、·_]+", "", text)


def report_code(name: str) -> str:
    text = normalized_stem(name)
    match = re.match(r"(\d{6}-\d{3}|\d{2}-\d{3}|\d{1,3})(?=[-\u4e00-\u9fff])", text)
    return match.group(1) if match else ""


def filename_bridge_key(name: str) -> str:
    text = normalized_stem(name)
    code = report_code(name)
    if code:
        text = text[len(code):].lstrip("-")
    text = re.sub(r"^20\d{2}年", "", text)
    text = re.sub(r"\d{8}修改后最后定稿|全|报告", "", text)
    return text.strip("-")


def bridge_group_keys(name: str) -> list[str]:
    """Conservative aliases: over-grouping is preferable to validation leakage."""
    key = filename_bridge_key(name)
    if not key:
        return []
    # Keep route/chainage identity for generic unnamed bridges and footbridges.
    named = bool(re.search(r"[\u4e00-\u9fff]", key)) and not any(token in key for token in ("无名", "人行天桥"))
    base = re.sub(r"k\d+\+\d+(?:\.\d+)?", "", key) if named else key
    base = base or key
    keys = {base}
    if named:
        keys.add(base.replace("长江", "").replace("公路", "").replace("立交", ""))
    return sorted(key for key in keys if key)


def _year(path: str) -> str:
    return next((part for part in PurePosixPath(path).parts if re.fullmatch(r"20\d{2}年", part)), "")


def training_inventory(source: str | Path) -> tuple[list[str], list[str]]:
    path = Path(source)
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as handle:
            names = [item.filename for item in handle.infolist() if not item.is_dir()]
    else:
        names = [item.as_posix() for item in path.rglob("*") if item.is_file()]
    names = sorted(name.replace("\\", "/") for name in names if PurePosixPath(name).suffix.lower() in {".doc", ".docx"})
    return (
        [name for name in names if "/赛题一_训练集/" in name],
        [name for name in names if "/赛题一_训练集标签/" in name],
    )


def pair_training_files(originals: list[str], labels: list[str]) -> dict[str, Any]:
    exact: dict[tuple[str, str], list[str]] = defaultdict(list)
    codes: dict[tuple[str, str], list[str]] = defaultdict(list)
    for name in labels:
        exact[(_year(name), normalized_stem(name))].append(name)
        if report_code(name):
            codes[(_year(name), report_code(name))].append(name)
    proposed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unresolved = []
    for original in sorted(originals):
        candidates = exact[(_year(original), normalized_stem(original))]
        method, similarity = "normalized_name", 1.0
        if not candidates and report_code(original):
            candidates = codes[(_year(original), report_code(original))]
            method = "unique_year_code_and_name"
            if len(candidates) == 1:
                left, right = filename_bridge_key(original), filename_bridge_key(candidates[0])
                similarity = SequenceMatcher(None, left, right).ratio()
                if similarity < 0.85:
                    candidates = []
        if len(candidates) != 1:
            unresolved.append({"train_file": original, "reason": "missing_or_ambiguous_label", "candidates": candidates})
            continue
        label = candidates[0]
        proposed[label].append({"train_file": original, "label_file": label, "year": _year(original), "match_method": method, "name_similarity": round(similarity, 6)})
    pairs = []
    for label, items in sorted(proposed.items()):
        if len(items) == 1:
            pairs.extend(items)
        else:
            unresolved.extend({"train_file": item["train_file"], "reason": "many_to_one_label", "candidates": [label]} for item in items)
    used = {item["label_file"] for item in pairs}
    return {"train_count": len(originals), "label_count": len(labels), "paired_count": len(pairs), "pairs": sorted(pairs, key=lambda item: item["train_file"]), "unmatched": unresolved, "unused_labels": sorted(set(labels) - used)}


def build_group_split(pairs: list[dict[str, Any]], label_names: dict[str, str] | None = None, validation_fraction: float = 0.2, seed: str = "city-inspection-v1") -> dict[str, Any]:
    if not 0 < validation_fraction < 1:
        raise ValueError("validation_fraction must be between 0 and 1")
    parents = list(range(len(pairs)))

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    aliases: dict[str, int] = {}
    keys_by_pair = []
    for index, item in enumerate(pairs):
        names = [item["train_file"], item["label_file"]]
        if label_names and label_names.get(item["label_file"]):
            names.append(label_names[item["label_file"]])
        keys = sorted({key for name in names for key in bridge_group_keys(name)})
        if not keys:
            raise ValueError(f"Missing bridge identity: {item['train_file']}")
        keys_by_pair.append(keys)
        for key in keys:
            if key in aliases:
                parents[root(index)] = root(aliases[key])
            else:
                aliases[key] = index
    components: dict[int, list[int]] = defaultdict(list)
    for index in range(len(pairs)):
        components[root(index)].append(index)
    if len(components) < 2:
        raise ValueError("At least two bridge groups are needed for a holdout")
    group_items = []
    for indices in components.values():
        keys = sorted({key for index in indices for key in keys_by_pair[index]})
        identity = hashlib.sha256("|".join(keys).encode("utf-8")).hexdigest()[:16]
        rank = hashlib.sha256(f"{seed}:{identity}".encode("utf-8")).hexdigest()
        group_items.append((rank, identity, indices, keys))
    group_items.sort()
    holdout_count = max(1, min(len(group_items) - 1, round(len(group_items) * validation_fraction)))
    train, validation, groups = [], [], []
    for index, (_, identity, indices, keys) in enumerate(group_items):
        split = "validation" if index < holdout_count else "train"
        target = validation if split == "validation" else train
        target.extend({**pairs[i], "bridge_group": identity} for i in indices)
        groups.append({"bridge_group": identity, "aliases": keys, "split": split, "reports": len(indices)})
    return {"strategy": "connected_bridge_alias_groups", "seed": seed, "validation_fraction": validation_fraction, "train_count": len(train), "validation_count": len(validation), "group_count": len(groups), "train": train, "validation": validation, "groups": groups, "note": "Conservative identity grouping; no claims about official scoring. Labels are for offline training/evaluation only."}


def write_inventory(source: str | Path, cache_dir: str | Path) -> dict[str, Any]:
    originals, labels = training_inventory(source)
    inventory = {"source": str(Path(source).resolve()), **pair_training_files(originals, labels)}
    target = Path(cache_dir)
    target.mkdir(parents=True, exist_ok=True)
    (target / "train_label_pairs.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
    return inventory
