"""冻结快照、只向前的回测窗口与统一评分。"""
import json
import hashlib
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "research_20261029"
ORIGINS = ["2025-10-12", "2026-01-27", "2026-05-19", "2026-07-28", "2026-08-28", "2026-09-28"]
HORIZON = 30


def load_snapshot():
    data = np.load(OUT / "snapshot.npz")
    meta = json.loads((OUT / "snapshot_metadata.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256((OUT / "snapshot.npz").read_bytes()).hexdigest()
    if digest != meta["snapshot_sha256"]:
        raise RuntimeError("快照文件与元数据不一致，停止评估。")
    binding = OUT / "benchmark_snapshot.json"
    if binding.exists() and json.loads(binding.read_text(encoding="utf-8"))["snapshot_sha256"] != digest:
        raise RuntimeError("当前目录已有其他快照的回测结果，请使用新的研究输出目录，避免混用缓存。")
    if not binding.exists():
        binding.write_text(json.dumps({"snapshot_sha256":digest,"origins":ORIGINS,"horizon":HORIZON},indent=2),encoding="utf-8")
    return data["y"], data["observed"], pd.DatetimeIndex(data["dates"]), data["keys"], meta


def eligible_mask(keys, config):
    included = np.ones(len(keys), dtype=bool)
    for item in config.get("excluded_channels", []):
        aliases = {item.get("channel_id"), item.get("channel_name")} - {None, ""}
        included &= ~((keys[:, 1] == item["platform_id"]) & np.isin(keys[:, 2], list(aliases)))
    return included


def metrics(actual, predicted):
    actual, predicted = np.asarray(actual, float), np.asarray(predicted, float)
    valid = np.isfinite(actual) & np.isfinite(predicted)
    a, p = actual[valid], predicted[valid]
    total = float(a.sum())
    return {"wape": float(np.abs(a-p).sum()/total*100) if total > 0 else None,
            "bias_pct": float((p-a).sum()/total*100) if total > 0 else None,
            "actual": total, "predicted": float(p.sum()), "mae": float(np.abs(a-p).mean()) if len(a) else None,
            "absolute_error": float(np.abs(a-p).sum()), "n": int(len(a))}


def sparse_frame(y, observed, dates, keys, end, included):
    day, series = np.where(observed[:end+1] & included[None, :])
    return pd.DataFrame({"date": dates[day], "shop_id": keys[series, 0], "platform": keys[series, 1],
                         "channel": keys[series, 2], "order_count": y[day, series], "is_sample": 0})
