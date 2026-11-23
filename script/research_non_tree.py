"""六个独立30天外层窗口，前三个30天内层窗口选择实验组合；测试期不调参。"""
import os
os.environ.setdefault("OPENBLAS_NUM_THREADS", "2")
os.environ.setdefault("OMP_NUM_THREADS", "2")
import json
import sys
import time
import numpy as np
from research_common import ROOT,OUT,ORIGINS,HORIZON,load_snapshot,eligible_mask,metrics
sys.path.insert(0,str(ROOT/"ecom_forecast"))
from config.routing_config import load_from_dict
from src.models.adaptive_demand import candidate_forecasts,adaptive_combination,calendar_design,profile_history


if __name__ == "__main__":
    y,observed,dates,keys,meta = load_snapshot()
    load_from_dict(meta["config"])
    included = eligible_mask(keys,meta["config"])
    cache = {}

    def predict_at(cutoff):
        if cutoff not in cache:
            all_dates = dates[:cutoff+HORIZON+1]
            design,phases = calendar_design(all_dates)
            cache[cutoff] = candidate_forecasts(y[:cutoff+1],dates[:cutoff+1],dates[cutoff+1:cutoff+HORIZON+1],design,phases)
        return cache[cutoff]

    for origin in ORIGINS:
        started = time.monotonic()
        cutoff = dates.get_loc(origin)
        validations = []
        for days_back in (90,60,30):
            index = cutoff-days_back
            pred = predict_at(index)
            validations.append((y[index+1:index+31],pred))
        pred = dict(predict_at(cutoff))
        pred["adaptive_non_tree"],selection = adaptive_combination(pred,validations,y[:cutoff+1],keys)
        np.savez_compressed(OUT/f"non_tree_{origin}.npz",**pred)
        np.savez_compressed(OUT/f"selection_{origin}.npz",names=np.array(selection["names"]),weights=selection["weights"],scores=selection["scores"],tags=selection["profile"]["tag"])
        print("NON_TREE",origin,"seconds",round(time.monotonic()-started,1),metrics(y[cutoff+1:cutoff+31,included],pred["adaptive_non_tree"][:,included]),flush=True)
    profile = profile_history(y,observed)
    records = [{"shop_id":str(key[0]),"platform":str(key[1]),"channel":str(key[2]),"included":bool(included[i]),
                **{name:(str(values[i]) if name=="tag" else float(values[i])) for name,values in profile.items()}}
               for i,key in enumerate(keys)]
    (OUT/"series_profiles.json").write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding="utf-8")
