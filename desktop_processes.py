"""Windows Job Object：在执行任何子进程代码前，将它纳入窗口的进程组。"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as w
import os
from pathlib import Path
import subprocess
import threading


class BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong), ("LimitFlags", w.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", w.DWORD), ("Affinity", ctypes.c_size_t),
        ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD),
    ]


class IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
    )]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", IoCounters)] + [
        (name, ctypes.c_size_t) for name in (
            "ProcessMemoryLimit", "JobMemoryLimit", "PeakProcessMemoryUsed", "PeakJobMemoryUsed",
        )
    ]


class StartupInfo(ctypes.Structure):
    _fields_ = [("cb", w.DWORD), ("lpReserved", w.LPWSTR), ("lpDesktop", w.LPWSTR),
                ("lpTitle", w.LPWSTR)] + [
        (name, w.DWORD) for name in (
            "dwX", "dwY", "dwXSize", "dwYSize", "dwXCountChars", "dwYCountChars", "dwFillAttribute", "dwFlags",
        )
    ] + [("wShowWindow", w.WORD), ("cbReserved2", w.WORD), ("lpReserved2", ctypes.POINTER(w.BYTE)),
         ("hStdInput", w.HANDLE), ("hStdOutput", w.HANDLE), ("hStdError", w.HANDLE)]


class ProcessInfo(ctypes.Structure):
    _fields_ = [("hProcess", w.HANDLE), ("hThread", w.HANDLE),
                ("dwProcessId", w.DWORD), ("dwThreadId", w.DWORD)]


class ManagedProcess:
    def __init__(self, kernel, handle, pid):
        self.kernel, self.handle, self.pid = kernel, handle, pid

    def poll(self):
        code = w.DWORD()
        if not self.handle:
            return 0
        if not self.kernel.GetExitCodeProcess(self.handle, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return None if code.value == 259 else code.value


class ProcessJob:
    def __init__(self):
        if os.name != "nt":
            raise RuntimeError("桌面开发模式的进程管理仅支持 Windows。")
        self.kernel = k = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, w.LPCWSTR], w.HANDLE),
            "SetInformationJobObject": ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD], w.BOOL),
            "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
            "CreateProcessW": ([w.LPCWSTR, w.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, w.BOOL,
                                w.DWORD, ctypes.c_void_p, w.LPCWSTR,
                                ctypes.POINTER(StartupInfo), ctypes.POINTER(ProcessInfo)], w.BOOL),
            "ResumeThread": ([w.HANDLE], w.DWORD),
            "TerminateProcess": ([w.HANDLE, w.UINT], w.BOOL),
            "TerminateJobObject": ([w.HANDLE, w.UINT], w.BOOL),
            "GetExitCodeProcess": ([w.HANDLE, ctypes.POINTER(w.DWORD)], w.BOOL),
            "WaitForSingleObject": ([w.HANDLE, w.DWORD], w.DWORD),
            "CloseHandle": ([w.HANDLE], w.BOOL),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(k, name)
            fn.argtypes, fn.restype = args, result
        self.lock = threading.RLock()
        self.processes = []
        self.handle = k.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        if not k.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            k.CloseHandle(self.handle)
            self.handle = None
            raise error

    def spawn(self, args, *, cwd: Path, log: Path, env=None):
        import msvcrt

        with self.lock:
            if not self.handle:
                raise RuntimeError("窗口已关闭，不能启动新的服务。")
            log.parent.mkdir(parents=True, exist_ok=True)
            with open(log, "ab", buffering=0) as output, open(os.devnull, "rb") as stdin:
                handles = [msvcrt.get_osfhandle(f.fileno()) for f in (stdin, output)]
                for handle in handles:
                    os.set_handle_inheritable(handle, True)
                startup = StartupInfo()
                startup.cb = ctypes.sizeof(startup)
                startup.dwFlags = 0x100  # STARTF_USESTDHANDLES
                startup.hStdInput, startup.hStdOutput, startup.hStdError = handles[0], handles[1], handles[1]
                info = ProcessInfo()
                command = ctypes.create_unicode_buffer(subprocess.list2cmdline([str(a) for a in args]))
                environment = ctypes.create_unicode_buffer("\0".join(
                    f"{key}={value}" for key, value in sorted((env or os.environ).items(), key=lambda item: item[0].upper())
                ) + "\0\0")
                try:
                    # 暂停创建 -> 绑定 Job -> 恢复；reloader/esbuild 等后代自动继承 Job。
                    created = self.kernel.CreateProcessW(
                        str(args[0]), command, None, None, True,
                        0x4 | 0x08000000 | 0x400,  # SUSPENDED | NO_WINDOW | UNICODE_ENVIRONMENT
                        environment, str(cwd), ctypes.byref(startup), ctypes.byref(info),
                    )
                finally:
                    for handle in handles:
                        os.set_handle_inheritable(handle, False)
                if not created:
                    raise ctypes.WinError(ctypes.get_last_error())
                try:
                    if not self.kernel.AssignProcessToJobObject(self.handle, info.hProcess):
                        raise ctypes.WinError(ctypes.get_last_error())
                    if self.kernel.ResumeThread(info.hThread) == 0xFFFFFFFF:
                        raise ctypes.WinError(ctypes.get_last_error())
                except BaseException:
                    self.kernel.TerminateProcess(info.hProcess, 1)
                    self.kernel.CloseHandle(info.hProcess)
                    raise
                finally:
                    self.kernel.CloseHandle(info.hThread)
                process = ManagedProcess(self.kernel, info.hProcess, info.dwProcessId)
                self.processes.append(process)
                return process

    def close(self):
        with self.lock:
            if not self.handle:
                return
            # 全树终止，包括已脱离原父子层级的重载进程，不扫描或误杀其他项目。
            self.kernel.TerminateJobObject(self.handle, 0)
            self.kernel.CloseHandle(self.handle)
            self.handle = None
            for process in self.processes:
                self.kernel.WaitForSingleObject(process.handle, 5000)
                self.kernel.CloseHandle(process.handle)
                process.handle = None
