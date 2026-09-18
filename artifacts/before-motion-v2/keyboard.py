"""Foreground-scoped Windows scan-code input with a freshness watchdog."""

import ctypes
import logging
from ctypes import wintypes
from pathlib import PureWindowsPath
import threading
import time

from .diagnostics import integrity_level


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("type", wintypes.DWORD), ("data", INPUTUNION)]


class WindowsInput:
    def __init__(self):
        self.integrity = integrity_level()
        self.target_integrity = {}
        self.foreground_pid = 0
        self.integrity_pid = 0
        self.integrity_checked = 0.0
        self.sent_count = 0
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.user.GetForegroundWindow.restype = wintypes.HWND
        self.user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.user.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
        self.user.SendInput.restype = wintypes.UINT
        self.user.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self.user.GetAsyncKeyState.restype = ctypes.c_short

    def foreground(self):
        hwnd = self.user.GetForegroundWindow()
        pid = wintypes.DWORD()
        self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        self.foreground_pid = pid.value
        handle = self.kernel.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(32768)
            path = ctypes.create_unicode_buffer(size.value)
            if self.kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
                return PureWindowsPath(path.value).name.lower()
            return ""
        finally:
            self.kernel.CloseHandle(handle)

    def emergency(self):
        return bool(self.user.GetAsyncKeyState(0x77) & 0x8000)  # F8

    def check_access(self):
        now = time.monotonic()
        if self.integrity_pid != self.foreground_pid or now - self.integrity_checked > 2:
            self.target_integrity = integrity_level(self.foreground_pid) if self.foreground_pid else {}
            self.integrity_pid = self.foreground_pid
            self.integrity_checked = now
        own = self.integrity.get("rid")
        target = self.target_integrity.get("rid")
        if own is not None and target is not None and target > own:
            raise PermissionError("Oyun yönetici yetkisinde. WebMotion'u ‘Yönetici olarak yeniden aç’ ile başlatın.")

    def send(self, scan, down):
        event = INPUT(type=1, ki=KEYBDINPUT(wScan=scan, dwFlags=0x0008 | (0 if down else 0x0002)))
        ctypes.set_last_error(0)
        sent = self.user.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT))
        error = ctypes.get_last_error()
        logging.info("SendInput scan=0x%02X down=%s accepted=%s error=%s target_pid=%s", scan, down, sent, error, self.foreground_pid)
        if sent != 1:
            raise OSError(error, "Windows girdiyi kabul etmedi (SendInput=0). Yetki farkı/UIPI hata kodu 0 ile de engelleyebilir.")
        self.sent_count += 1


class InputGuard(threading.Thread):
    SHIFT, SPACE = 0x2A, 0x39

    def __init__(self, backend=None):
        super().__init__(daemon=True, name="input-watchdog")
        self.backend = backend or WindowsInput()
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.enabled = False
        self.target = "spider-man.exe"
        self.stamp = 0.0
        self.swing = self.jump = False
        self.held = set()
        self.space_until = 0.0
        self.status = "Kontroller kapalı"
        self.error = ""
        self.test_due = None
        self.test_until = None
        self.test_result = ""

    def configure(self, enabled, target=None):
        with self.lock:
            self.enabled = enabled
            if enabled:
                self.error = ""
            if target:
                self.target = target.strip().lower()
            self.stamp = 0
            self.jump = self.swing = False
            self.test_due = self.test_until = None
            self.test_result = ""

    def schedule_test(self, target, now=None):
        self.configure(False, target)
        with self.lock:
            self.test_due = (time.monotonic() if now is None else now) + 5
            self.error = ""
            logging.info("Manual SPACE test scheduled target=%s", self.target)

    def check_access(self):
        check = getattr(self.backend, "check_access", None)
        if check:
            check()

    def publish(self, swing, jump, stamp):
        with self.lock:
            self.swing, self.stamp = swing, stamp
            self.jump = self.jump or jump

    def key(self, code, down):
        if down != (code in self.held):
            self.backend.send(code, down)
            if down:
                self.held.add(code)
            else:
                self.held.discard(code)

    def release(self):
        failure = None
        for code in tuple(self.held):
            try:
                self.key(code, False)
            except OSError as exc:
                failure = exc
        self.space_until = 0
        if failure:
            raise failure

    def step(self, now):
        with self.lock:
            if self.backend.emergency():
                self.enabled = False
                self.test_due = self.test_until = None
            if self.test_due is not None or self.test_until is not None:
                self.test_step(now)
                return
            active = self.enabled and now - self.stamp < 0.30
            focused = self.backend.foreground() == self.target if self.enabled else False
            jump, self.jump = self.jump, False
            if not active or not focused:
                self.release()
                self.status = (self.test_result or "Kontroller kapalı") if not self.enabled else ("Oyun penceresi bekleniyor" if not focused else "Güncel kamera verisi bekleniyor")
                return
            self.check_access()
            self.key(self.SHIFT, self.swing)
            if jump:
                self.space_until = now + 0.065
            self.key(self.SPACE, now < self.space_until)
            self.status = "Windows'a SHIFT-down gönderildi" if self.swing else "Hedef oyun ön planda · hareket bekleniyor"

    def test_step(self, now):
        if self.test_due is not None and now < self.test_due:
            self.release()
            self.status = f"SPACE testi: {max(1, int(self.test_due - now) + 1)} sn · oyuna geçin"
            return
        focused = self.backend.foreground() == self.target
        if not focused:
            self.release()
            self.test_due = self.test_until = None
            self.error = "SPACE testi iptal: hedef oyun ön planda değil."
            return
        self.check_access()
        if self.test_due is not None:
            self.key(self.SPACE, True)
            self.test_due = None
            self.test_until = now + 0.15
            self.status = "Windows'a test SPACE-down gönderildi"
        elif now >= self.test_until:
            self.release()
            self.test_until = None
            self.status = "SPACE testi gönderildi · oyun tepkisini kontrol edin"
            self.test_result = self.status
            logging.info("Manual SPACE test completed")

    def run(self):
        try:
            while not self.stop_event.is_set():
                try:
                    self.step(time.monotonic())
                except Exception as exc:
                    logging.exception("Input dispatch failed")
                    self.error = str(exc)
                    self.configure(False)
                self.stop_event.wait(0.01)
        finally:
            try:
                self.release()
            except OSError as exc:
                self.error = str(exc)

    def close(self):
        self.stop_event.set()
        if self.is_alive():
            self.join(timeout=1)
