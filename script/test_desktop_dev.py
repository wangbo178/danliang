"""Windows Job 的进程树回收、宿主崩溃回收与开发启动取消检查。"""
import ctypes
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from desktop_processes import ProcessJob
from desktop_dev import DevelopmentServer

CHILD = """
import subprocess,sys,os,json,time
from pathlib import Path
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
Path(sys.argv[1]).write_text(json.dumps([os.getpid(), child.pid]))
time.sleep(120)
"""
OWNER = """
import sys,time
from pathlib import Path
from desktop_processes import ProcessJob
job = ProcessJob()
job.spawn([sys.executable, '-c', sys.argv[2], sys.argv[1]], cwd=Path.cwd(), log=Path(sys.argv[1]+'.log'))
time.sleep(120)
"""


def wait_pids(path):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            time.sleep(0.05)
    raise TimeoutError("测试子进程未启动")


def open_process(pid):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    kernel.WaitForSingleObject.restype = w.DWORD
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    handle = kernel.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    return kernel, handle


@unittest.skipUnless(os.name == "nt", "Windows only")
class DevelopmentTests(unittest.TestCase):
    def assert_dead(self, handles):
        for kernel, handle in handles:
            try:
                self.assertEqual(kernel.WaitForSingleObject(handle, 5000), 0)
            finally:
                kernel.CloseHandle(handle)

    def test_close_kills_descendants_and_preserves_unrelated_process(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pids.json"
            job = ProcessJob()
            unrelated = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(120)"],
                                         creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                job.spawn([sys.executable, "-c", CHILD, path], cwd=ROOT, log=path.with_suffix(".log"))
                handles = [open_process(pid) for pid in wait_pids(path)]
                job.close()
                job.close()  # 重复关闭也是安全的。
                self.assert_dead(handles)
                self.assertIsNone(unrelated.poll())
            finally:
                job.close()
                unrelated.terminate()
                unrelated.wait(timeout=5)

    def test_owner_crash_kills_descendants(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pids.json"
            owner = subprocess.Popen([sys.executable, "-c", OWNER, str(path), CHILD], cwd=ROOT,
                                     creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                handles = [open_process(pid) for pid in wait_pids(path)]
                owner.terminate()
                owner.wait(timeout=5)
                self.assert_dead(handles)
            finally:
                if owner.poll() is None:
                    owner.terminate()
                    owner.wait(timeout=5)

    def test_cancelled_start_does_not_launch_services(self):
        runtime = DevelopmentServer()
        cancelled = threading.Event()
        cancelled.set()
        try:
            self.assertFalse(runtime.start(cancelled))
            self.assertEqual(runtime.job.processes, [])
        finally:
            runtime.stop()

    def test_close_during_backend_startup(self):
        runtime = DevelopmentServer()
        cancelled = threading.Event()
        timer = threading.Timer(0.3, cancelled.set)
        timer.start()
        try:
            self.assertFalse(runtime.start(cancelled))
            self.assertIsNone(runtime.frontend)
            handles = [open_process(process.pid) for process in runtime.job.processes]
        finally:
            cancelled.set()
            timer.cancel()
            runtime.stop()
        self.assert_dead(handles)


if __name__ == "__main__":
    unittest.main()
