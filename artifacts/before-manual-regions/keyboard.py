"""Foreground-scoped Windows scan-code input with a freshness watchdog."""

import ctypes
import logging
from ctypes import wintypes
from pathlib import PureWindowsPath
import threading
import time

from .diagnostics import integrity_level
from .gestures import Decision


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
        self.path_pid = 0
        self.path_name = ""
        self.path_checked = 0.0
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
        now = time.monotonic()
        if pid.value == self.path_pid and now - self.path_checked < 1:
            return self.path_name
        handle = self.kernel.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(32768)
            path = ctypes.create_unicode_buffer(size.value)
            if self.kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
                self.path_pid, self.path_checked = pid.value, now
                self.path_name = PureWindowsPath(path.value).name.lower()
                return self.path_name
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

    def mouse(self, button, down):
        flags = {("left", True): 0x0002, ("left", False): 0x0004,
                 ("right", True): 0x0008, ("right", False): 0x0010}
        self.mouse_event(flags[(button, down)])
        logging.info("Mouse button=%s down=%s accepted=1", button, down)

    def move(self, dx, dy):
        if dx or dy:
            self.mouse_event(0x0001, dx, dy)

    def mouse_event(self, flags, dx=0, dy=0):
        event = INPUT(type=0, mi=MOUSEINPUT(dx=dx, dy=dy, dwFlags=flags))
        ctypes.set_last_error(0)
        if self.user.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT)) != 1:
            raise OSError(ctypes.get_last_error(), "Windows fare girdisini kabul etmedi.")
        self.sent_count += 1


class InputGuard(threading.Thread):
    SHIFT, SPACE = 0x2A, 0x39
    A, D, E, CTRL = 0x1E, 0x20, 0x12, 0x1D

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
        self.decision = Decision()
        self.pending = set()
        self.pending_mouse = [0.0, 0.0]
        self.pulses = {}
        self.mouse_held = set()
        self.mouse_remainder = [0.0, 0.0]
        self.camera_speed = 600.0
        self.invert_x = False
        self.swing_active = False
        self.swing_shift_at = None

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
            self.decision = Decision()
            self.pending.clear()
            self.pending_mouse = [0.0, 0.0]

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
        self.publish_decision(Decision(swing=swing, jump=jump), stamp)

    def publish_decision(self, decision, stamp):
        with self.lock:
            if stamp <= self.stamp:
                return
            if decision.aim and self.enabled:
                self.pending_mouse[0] += decision.aim_dx
                self.pending_mouse[1] += decision.aim_dy
            else:
                self.pending_mouse = [0.0, 0.0]
            self.decision, self.stamp = decision, stamp
            self.swing, self.jump = decision.swing, decision.jump
            for action in ("jump", "dodge", "gadget", "attack"):
                if getattr(decision, action):
                    self.pending.add(action)

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
        self.swing_active = False
        self.swing_shift_at = None
        for button in tuple(self.mouse_held):
            try:
                self.mouse_button(button, False)
            except OSError as exc:
                failure = exc
        self.pulses.clear()
        self.mouse_remainder = [0.0, 0.0]
        if failure:
            raise failure

    def mouse_button(self, button, down):
        if down != (button in self.mouse_held):
            self.backend.mouse(button, down)
            if down:
                self.mouse_held.add(button)
            else:
                self.mouse_held.discard(button)

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
            pending, self.pending = self.pending, set()
            mouse_delta, self.pending_mouse = self.pending_mouse, [0.0, 0.0]
            if not active or not focused:
                self.release()
                self.status = (self.test_result or "Kontroller kapalı") if not self.enabled else ("Oyun penceresi bekleniyor" if not focused else "Güncel kamera verisi bekleniyor")
                return
            self.check_access()
            decision = self.decision
            wants_swing = decision.swing and not decision.aim
            if wants_swing and not self.swing_active:
                # One jump on entry, with a real key-up before holding Shift.
                self.key(self.SHIFT, False)
                self.key(self.SPACE, False)
                self.space_until = now + 0.10
                self.swing_shift_at = now + 0.16
                self.swing_active = True
            elif not wants_swing and self.swing_active:
                self.swing_active = False
                self.swing_shift_at = None
                self.space_until = 0
            if "jump" in pending and "dodge" not in pending and not wants_swing:
                self.space_until = now + 0.065
            self.key(self.SPACE, now < self.space_until)
            self.key(self.SHIFT, wants_swing and self.swing_shift_at is not None and now >= self.swing_shift_at)
            for action in ("dodge", "gadget", "attack"):
                if action in pending and (action != "gadget" or decision.aim) and (action != "attack" or not decision.aim):
                    self.pulses[action] = now + 0.10
            self.key(self.CTRL, now < self.pulses.get("dodge", 0))
            self.key(self.E, decision.aim and now < self.pulses.get("gadget", 0))
            self.mouse_button("right", decision.aim)
            self.mouse_button("left", not decision.aim and now < self.pulses.get("attack", 0))
            steer = decision.steer * (-1 if self.invert_x else 1)
            # Release the opposing direction first; never hold A and D together.
            left = wants_swing and steer < (-0.18 if self.A in self.held else -0.30)
            right = wants_swing and steer > (0.18 if self.D in self.held else 0.30)
            if not left:
                self.key(self.A, False)
            if not right:
                self.key(self.D, False)
            if left:
                self.key(self.A, True)
            if right:
                self.key(self.D, True)
            if decision.aim:
                mouse_delta[0] *= -1 if self.invert_x else 1
                for i, value in enumerate(mouse_delta):
                    self.mouse_remainder[i] += value * self.camera_speed
                dx, dy = (int(value) for value in self.mouse_remainder)
                if dx or dy:
                    self.backend.move(dx, dy)
                    self.mouse_remainder[0] -= dx
                    self.mouse_remainder[1] -= dy
            else:
                self.mouse_remainder = [0.0, 0.0]
            self.status = "Hedef ön planda · " + decision.description()
            if wants_swing:
                self.status += " · " + ("SHIFT basılı" if self.SHIFT in self.held else "SPACE → SHIFT hazırlanıyor")

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
