"""合成反例：定位当前特征键是否混合平台。不连接数据库、不训练或保存模型。"""
import json
from pathlib import Path
import sys
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"ecom_forecast"))
from src.models.channel_models import ChannelTypeModel

if __name__ == "__main__":
    frame = pd.DataFrame([
        {"date":day,"shop_id":"same-shop","platform":platform,"channel":"店铺","order_count":count}
        for day in pd.date_range("2026-01-01",periods=60)
        for platform,count in (("TM",1000),("TB",10))
    ])
    model = ChannelTypeModel("stable")
    mixed = model._prepare_features(frame,fit=True)
    isolated = frame.copy()
    isolated["shop_id"] = isolated["shop_id"]+"@@"+isolated["platform"]
    separate = ChannelTypeModel("stable")._prepare_features(isolated,fit=True)
    last = frame["date"].max()
    result = {
        "expected_lag7":{"TM":1000,"TB":10},
        "current_lag7":dict(zip(mixed[mixed["date"]==last]["platform"],mixed[mixed["date"]==last]["order_count_lag7"])),
        "platform_isolated_lag7":dict(zip(separate[separate["date"]==last]["platform"],separate[separate["date"]==last]["order_count_lag7"])),
    }
    result["collision_reproduced"] = result["current_lag7"] != result["expected_lag7"]
    assert result["platform_isolated_lag7"] == result["expected_lag7"]
    path = ROOT/"output/research_20261029/key_collision_proof.json"
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2))
