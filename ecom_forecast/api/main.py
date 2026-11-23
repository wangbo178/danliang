"""
FastAPI 主应用
"""
import sys
import os

# 让 api/ 旁边的 src/ 等包可被 import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import ORJSONResponse

from api.database import init_db
from api.routers import config, data, training, results, expected_gmv, operations, operations_agent
from api.services.feishu_schedule_sync import start_schedule_sync, stop_schedule_sync

app = FastAPI(
    title="BTN物流单量预测系统 API",
    version="2.0.0",
    description="BTN物流单量预测系统 REST API",
    # 大量预测明细使用 orjson 可显著降低 JSON 序列化开销。
    default_response_class=ORJSONResponse,
)

# 预测排期表通常有数 MB，压缩后再传输，减少页面等待和带宽占用。
app.add_middleware(GZipMiddleware, minimum_size=1_024, compresslevel=5)

# ── CORS ──────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 路由挂载 ──────────────────────────────────────────────────────────────
app.include_router(config.router,   prefix="/api/config",   tags=["配置"])
app.include_router(data.router,     prefix="/api/data",     tags=["数据"])
app.include_router(training.router, prefix="/api/training", tags=["训练"])
app.include_router(results.router,  prefix="/api/results",  tags=["结果"])
app.include_router(expected_gmv.router, prefix="/api/expected-gmv", tags=["预期GMV"])
app.include_router(operations.router, prefix="/api/operations", tags=["运营"])
app.include_router(operations_agent.router,prefix="/api/operations/agent",tags=["运营Agent"])

# ── WebSocket 路由（在 training router 中定义，此处确保可达）────────────────
# ws://localhost:8000/ws/training/{job_id}


@app.on_event("startup")
async def startup():
    init_db()
    from api.services.expected_gmv import recover_tasks
    recover_tasks()
    start_schedule_sync()
    from api.services.operations_agent import start_worker
    start_worker()


@app.on_event("shutdown")
async def shutdown():
    stop_schedule_sync()
    from api.services.operations_agent import stop_worker
    stop_worker()


@app.get("/api/health", tags=["系统"])
def health():
    return {"status": "ok", "version": "2.0.0"}
