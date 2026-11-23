"""桌面宿主的真实 HTTP 生命周期回归测试，不访问业务数据库。"""
import asyncio
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from desktop import DesktopServer, mount_frontend
from fastapi import FastAPI, WebSocket


class DesktopTests(unittest.TestCase):
    def test_routes_websocket_and_shutdown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "assets").mkdir()
            (root / "index.html").write_text("<h1>desktop-test</h1>", encoding="utf-8")
            (root / "assets" / "app.js").write_text("console.log('ok')", encoding="utf-8")
            app = FastAPI()

            @app.get("/api/health")
            async def health():
                return {"status": "ok"}

            @app.websocket("/api/training/ws/test")
            async def echo(ws: WebSocket):
                await ws.accept()
                await ws.send_text("ready")
                await ws.close()

            mount_frontend(app, root)
            server = DesktopServer(app)
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                self.assertTrue(server.start(threading.Event(), timeout=10))
                for path in ("/", "/dashboard", "/results"):
                    with opener.open(server.url + path) as response:
                        self.assertIn(b"desktop-test", response.read())
                with opener.open(server.url + "/assets/app.js") as response:
                    self.assertIn(b"console.log", response.read())
                for path in ("/api/missing", "/assets/missing.js", "/missing.js", "/%2e%2e/desktop.py"):
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        opener.open(server.url + path)
                    self.assertEqual(caught.exception.code, 404)

                async def check_ws():
                    from websockets.asyncio.client import connect
                    async with connect(server.url.replace("http:", "ws:") + "/api/training/ws/test") as ws:
                        self.assertEqual(await ws.recv(), "ready")

                asyncio.run(check_ws())
            finally:
                server.stop()
            self.assertFalse(server.thread.is_alive())
            with self.assertRaises(urllib.error.URLError):
                opener.open(server.url + "/api/health", timeout=1)

    def test_failed_startup_and_cancellation(self):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def fail(app):
            raise RuntimeError("expected startup failure")
            yield

        server = DesktopServer(FastAPI(lifespan=fail))
        try:
            with self.assertRaises(RuntimeError):
                server.start(threading.Event(), timeout=5)
        finally:
            server.stop()
        server = DesktopServer(FastAPI())
        cancelled = threading.Event()
        cancelled.set()
        try:
            self.assertFalse(server.start(cancelled))
        finally:
            server.stop()
        self.assertFalse(server.thread.is_alive())


if __name__ == "__main__":
    unittest.main()
