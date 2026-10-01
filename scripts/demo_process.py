"""Windows PID/executable/creation-time ownership checks without shell commands."""

import ctypes
from ctypes import wintypes


def process_identity(pid):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        if (
            ctypes.get_last_error() == 87
        ):  # ERROR_INVALID_PARAMETER: PID no longer exists.
            return None
        raise OSError("cannot verify process ownership")
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        kernel.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            raise OSError("cannot verify process executable")
        times = [wintypes.FILETIME() for _ in range(4)]
        kernel.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            *([ctypes.POINTER(wintypes.FILETIME)] * 4),
        ]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(item) for item in times)):
            raise OSError("cannot verify process creation time")
        created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        return {"pid": pid, "executable": buffer.value, "created": created}
    finally:
        kernel.CloseHandle(handle)


def stop_owned(record):
    actual = process_identity(record["pid"])
    if actual is None:
        return
    if actual != {key: record[key] for key in ("pid", "executable", "created")}:
        raise ValueError("process ownership mismatch; refusing stop")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        *([ctypes.POINTER(wintypes.FILETIME)] * 4),
    ]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1001, False, record["pid"])
    if not handle:
        raise OSError("cannot open owned process")
    try:
        # Recheck creation time on the actual termination handle to prevent PID reuse.
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(item) for item in times)):
            raise OSError("cannot recheck process")
        if (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime != record[
            "created"
        ]:
            raise ValueError("process reused; refusing stop")
        if not kernel.TerminateProcess(handle, 0):
            raise OSError("cannot stop owned process")
    finally:
        kernel.CloseHandle(handle)
