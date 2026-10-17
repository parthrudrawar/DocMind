"""OS memory guard for the disposable parser process, never the Streamlit host."""
from __future__ import annotations

import os


def unix_limits(memory_bytes, output_bytes, cpu_seconds):
    import resource

    for kind, limit in ((resource.RLIMIT_AS, memory_bytes), (resource.RLIMIT_FSIZE, output_bytes),
                        (resource.RLIMIT_CPU, cpu_seconds), (resource.RLIMIT_CORE, 0)):
        resource.setrlimit(kind, (limit, limit))


class WindowsJob:
    """All parser/OCR descendants share a memory cap and die when the handle closes."""

    def __init__(self, memory_bytes, cpu_seconds=60):
        import ctypes as c
        from ctypes import wintypes as w

        class Basic(c.Structure):
            _fields_ = [("process_time", c.c_longlong), ("job_time", c.c_longlong),
                        ("flags", w.DWORD), ("minimum_ws", c.c_size_t), ("maximum_ws", c.c_size_t),
                        ("active_processes", w.DWORD), ("affinity", c.c_size_t),
                        ("priority", w.DWORD), ("scheduling", w.DWORD)]

        class IO(c.Structure):
            _fields_ = [(name, c.c_ulonglong) for name in ("read_ops", "write_ops", "other_ops",
                                                         "read_bytes", "write_bytes", "other_bytes")]

        class Extended(c.Structure):
            _fields_ = [("basic", Basic), ("io", IO), ("process_memory", c.c_size_t),
                        ("job_memory", c.c_size_t), ("peak_process", c.c_size_t), ("peak_job", c.c_size_t)]

        self.api = c.WinDLL("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.argtypes, self.api.CreateJobObjectW.restype = [c.c_void_p, w.LPCWSTR], w.HANDLE
        self.api.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
        self.api.SetInformationJobObject.restype = w.BOOL
        self.api.OpenProcess.argtypes, self.api.OpenProcess.restype = [w.DWORD, w.BOOL, w.DWORD], w.HANDLE
        self.api.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self.api.AssignProcessToJobObject.restype = w.BOOL
        self.api.CloseHandle.argtypes, self.api.CloseHandle.restype = [w.HANDLE], w.BOOL
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError("Parser memory guard unavailable")
        info = Extended()
        info.basic.flags = 0x2000 | 0x100 | 0x200 | 0x8 | 0x2 | 0x4
        # Kill on close; process/job memory, process count, process/job CPU time.
        info.basic.process_time = info.basic.job_time = cpu_seconds * 10_000_000
        info.basic.active_processes = 2  # parser plus optional Tesseract
        info.process_memory = info.job_memory = memory_bytes
        if not self.api.SetInformationJobObject(self.handle, 9, c.byref(info), c.sizeof(info)):
            self.close()
            raise OSError("Parser memory guard unavailable")

    def attach(self, pid):
        process = self.api.OpenProcess(0x100 | 0x1, False, pid)  # SET_QUOTA and TERMINATE
        try:
            if not process or not self.api.AssignProcessToJobObject(self.handle, process):
                raise OSError("Parser memory guard unavailable")
        finally:
            if process:
                self.api.CloseHandle(process)

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def memory_guard(memory_bytes, cpu_seconds=60):
    return WindowsJob(memory_bytes, cpu_seconds) if os.name == "nt" else None
