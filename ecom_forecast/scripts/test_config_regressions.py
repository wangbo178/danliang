"""配置写入回归测试：平台编辑必须差量同步渠道。"""
import os
import sys

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import models, schemas  # noqa: E402
from api.database import Base  # noqa: E402
from api.routers.config import bootstrap_config, export_config, list_platforms, update_platform  # noqa: E402
from api.routers.training import _excluded_channel_mask  # noqa: E402
import pandas as pd  # noqa: E402


def main():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        platform = models.Platform(platform_id="dw", name="得物")
        session.add(platform)
        session.add(models.Channel(
            platform_id="dw", channel_id="dw_shelf",
            name="原名称", channel_type="stable",
        ))
        session.commit()
        loaded = list_platforms(session)
        assert loaded[0]["platform_id"] == "dw"
        assert loaded[0]["maintenance_mode"] == "manual"
        assert loaded[0]["channels"][0]["channel_id"] == "dw_shelf"
        assert loaded[0]["channels"][0]["maintenance_mode"] == "manual"
        snapshot = bootstrap_config(session)
        assert snapshot["platforms"][0]["platform_id"] == "dw"
        assert snapshot["shops"] == []

        original_id = session.query(models.Channel.id).filter_by(
            platform_id="dw", channel_id="dw_shelf"
        ).scalar()

        payload = schemas.PlatformCreate(
            platform_id="dw",
            name="得物平台",
            channels=[
                schemas.ChannelBase(channel_id="dw_shelf", name="得物货架", channel_type="stable"),
                schemas.ChannelBase(channel_id="dw_live", name="得物直播", channel_type="event",
                                    include_in_forecast=False),
            ],
        )
        response = update_platform("dw", payload, session)
        assert response["name"] == "得物平台"
        assert [channel["channel_id"] for channel in response["channels"]] == ["dw_live", "dw_shelf"]
        assert response["channels"][0]["include_in_forecast"] is False
        assert session.query(models.Channel.id).filter_by(
            platform_id="dw", channel_id="dw_shelf"
        ).scalar() == original_id
        assert session.query(models.Channel).filter_by(platform_id="dw").count() == 2
        exported = export_config(session)
        assert exported["excluded_channels"] == [{
            "platform_id": "dw", "channel_id": "dw_live", "channel_name": "得物直播"
        }]
        frame = pd.DataFrame([
            {"platform": "dw", "channel": "dw_live"},
            {"platform": "dw", "channel": "dw_shelf"},
            {"platform": "other", "channel": "dw_live"},
        ])
        assert _excluded_channel_mask(frame, exported["excluded_channels"]).tolist() == [True, False, False]

        duplicate = schemas.PlatformCreate(
            platform_id="dw", name="得物平台",
            channels=[
                schemas.ChannelBase(channel_id="dw_shelf"),
                schemas.ChannelBase(channel_id="DW_SHELF"),
            ],
        )
        try:
            update_platform("dw", duplicate, session)
            raise AssertionError("重复渠道应被拒绝")
        except HTTPException as exc:
            assert exc.status_code == 422
        assert session.query(models.Channel).filter_by(platform_id="dw").count() == 2
        print("[OK] platform channel update is differential and duplicate-safe")
    finally:
        session.close()
        engine.dispose()


if __name__ == "__main__":
    main()
