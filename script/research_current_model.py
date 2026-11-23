"""同一冻结快照重新训练现有实现；不加载/覆盖线上模型，不用测试期选择回退。"""
import os
os.environ.setdefault("OMP_NUM_THREADS", "4")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "2")
import copy
import json
import sys
import time
from pathlib import Path
import numpy as np
import pandas as pd

from research_common import ROOT, OUT, ORIGINS, HORIZON, load_snapshot, eligible_mask, sparse_frame, metrics
sys.path.insert(0, str(ROOT / "ecom_forecast"))
from config.model_config import CHANNEL_MODEL_PARAMS, TRAINING_CONFIG
from src.pipeline.forecast_pipeline import ForecastPipeline


if __name__ == "__main__":
    separate_platform = "--separate-platform" in sys.argv
    prefix = "identity" if separate_platform else "current"
    y, observed, dates, keys, meta = load_snapshot()
    included = eligible_mask(keys, meta["config"])
    for origin in ORIGINS:
        path = OUT / f"{prefix}_{origin}.npz"
        if path.exists():
            print("cached", origin, flush=True)
            continue
        cutoff = dates.get_loc(origin)
        train = sparse_frame(y, observed, dates, keys, cutoff, included)
        if separate_platform:
            train["shop_id"] = train["shop_id"].astype(str) + "@@" + train["platform"].astype(str)
        known = observed[:cutoff+1].any(axis=0) & included
        config = copy.deepcopy(meta["config"])
        config["channel_model_params"] = copy.deepcopy(CHANNEL_MODEL_PARAMS)
        for values in config["channel_model_params"].values():
            values["n_jobs"] = 4
        config["training_config"] = dict(TRAINING_CONFIG)
        pipeline = ForecastPipeline(model_dir=str(OUT / "unused_models"), config_dict=config)
        started = time.monotonic()
        print(f"{prefix.upper()} {origin}: fit rows={len(train)} series={known.sum()}", flush=True)
        model_cache = OUT / f"channel_cache_{prefix}_{origin}"
        if model_cache.exists():
            pipeline.channel_ensemble.load(str(model_cache))
        else:
            pipeline.train_channels(train, schedule_df=None, commission_df=None, anchor_df=None)
            pipeline.channel_ensemble.save(str(model_cache))
        day_ids = np.repeat(np.arange(cutoff+1, cutoff+1+HORIZON), known.sum())
        series_ids = np.tile(np.flatnonzero(known), HORIZON)
        future = pd.DataFrame({"date": dates[day_ids], "shop_id": keys[series_ids, 0],
                               "platform": keys[series_ids, 1], "channel": keys[series_ids, 2], "order_count": np.nan})
        if separate_platform:
            future["shop_id"] = future["shop_id"].astype(str) + "@@" + future["platform"].astype(str)
        original_predict = pipeline.channel_ensemble.predict
        counter = [0]

        def progress_predict(*args, **kwargs):
            try:
                value = original_predict(*args, **kwargs)
            except Exception:
                frame = args[0]
                print("PREDICTION FAILURE",prefix,origin,"step",counter[0]+1,
                      "date dtype",str(frame['date'].dtype),
                      "date sample",[repr(value) for value in frame['date'].head(4)],flush=True)
                raise
            counter[0] += 1
            if counter[0] % 5 == 0:
                print(f"{prefix.upper()} {origin}: predicted {counter[0]}/{HORIZON} days", flush=True)
            return value

        pipeline.channel_ensemble.predict = progress_predict
        prediction = pipeline.predict_channels(future, train)
        lookup = {(str(key[0])+"@@"+str(key[1]) if separate_platform else str(key[0]), str(key[1]), str(key[2])): i for i, key in enumerate(keys)}
        prediction_series = np.array([lookup[(str(row.shop_id), str(row.platform), str(row.channel))] for row in prediction.itertuples()])
        prediction_days = (pd.to_datetime(prediction["date"]) - dates[cutoff+1]).dt.days.to_numpy()
        arrays = {}
        for name, col in (("current", "predicted_orders"), ("current_raw", "raw_predicted_orders"), ("current_guardrail", "guardrail_predicted_orders")):
            values = np.zeros((HORIZON, len(keys)))
            values[prediction_days, prediction_series] = prediction[col].to_numpy(float)
            if not np.isfinite(values).all():
                raise ValueError(f"Nonfinite predictions: {origin} {name}")
            arrays[name] = values
        arrays["runtime_seconds"] = np.array(time.monotonic()-started)
        np.savez_compressed(path, **arrays)
        print(prefix.upper()+" DONE", origin, round(time.monotonic()-started, 1), metrics(y[cutoff+1:cutoff+31, included], arrays["current"][:, included]), flush=True)
