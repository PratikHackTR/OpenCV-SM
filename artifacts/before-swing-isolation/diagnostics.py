"""Read process integrity levels without opening game memory."""

import ctypes
from ctypes import wintypes
import os


def integrity_level(pid=None):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
    advapi.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
    advapi.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    advapi.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
    process = kernel.OpenProcess(0x1000, False, pid or os.getpid())
    if not process:
        return {"rid": None, "error": ctypes.get_last_error()}
    token = wintypes.HANDLE()
    try:
        if not advapi.OpenProcessToken(process, 0x0008, ctypes.byref(token)):
            return {"rid": None, "error": ctypes.get_last_error()}
        size = wintypes.DWORD()
        advapi.GetTokenInformation(token, 25, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, 25, buffer, size, ctypes.byref(size)):
            return {"rid": None, "error": ctypes.get_last_error()}
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        count = advapi.GetSidSubAuthorityCount(sid)[0]
        rid = advapi.GetSidSubAuthority(sid, count - 1)[0]
        name = "System" if rid >= 0x4000 else "High" if rid >= 0x3000 else "Medium" if rid >= 0x2000 else "Low"
        return {"rid": rid, "name": name, "error": 0}
    finally:
        if token:
            kernel.CloseHandle(token)
        kernel.CloseHandle(process)
