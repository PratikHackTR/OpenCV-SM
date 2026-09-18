"""Verify real SendInput delivery to a dedicated local Qt receiver, not the game."""

import ctypes
import json
from pathlib import Path
from ctypes import wintypes
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QLabel
from webmotion.keyboard import WindowsInput, InputGuard
from webmotion.gestures import Decision


class Receiver(QLabel):
    def __init__(self):
        super().__init__("WebMotion input check\nTesting keyboard, mouse buttons and relative motion in this window.")
        self.events = []
        self.event_times = []
        self.mouse_events = []
        self.moves = 0
        self.setMouseTracking(True)
        self.setWindowTitle("WebMotion — Input diagnostic receiver")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.resize(540, 180)

    def keyPressEvent(self, event):
        self.events.append([event.key(), "down"])
        self.event_times.append(time.monotonic())

    def keyReleaseEvent(self, event):
        self.events.append([event.key(), "up"])
        self.event_times.append(time.monotonic())

    def mousePressEvent(self, event):
        self.mouse_events.append([event.button().name, "down"])

    def mouseReleaseEvent(self, event):
        self.mouse_events.append([event.button().name, "up"])

    def mouseMoveEvent(self, event):
        self.moves += 1


def main():
    app = QApplication([])
    receiver = Receiver()
    backend = WindowsInput()
    backend.user.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    result = {"integrity": backend.integrity}
    held = set()
    buttons = set()
    receiver.show()
    receiver.activateWindow()
    receiver.setFocus()
    backend.user.SetForegroundWindow(int(receiver.winId()))
    saved_cursor = wintypes.POINT()
    backend.user.GetCursorPos(ctypes.byref(saved_cursor))

    def send(scan, down):
        if down and backend.user.GetForegroundWindow() != int(receiver.winId()):
            result["error"] = "Receiver lost foreground; down event skipped."
            return
        if not down and scan not in held:
            return
        backend.foreground()
        backend.check_access()
        backend.send(scan, down)
        held.add(scan) if down else held.discard(scan)

    expected = []
    for i, (scan, key) in enumerate(((0x2A, Qt.Key.Key_Shift), (0x39, Qt.Key.Key_Space),
                                   (0x12, Qt.Key.Key_E), (0x1D, Qt.Key.Key_Control),
                                   (0x1E, Qt.Key.Key_A), (0x20, Qt.Key.Key_D))):
        QTimer.singleShot(400 + i * 240, lambda s=scan: send(s, True))
        QTimer.singleShot(520 + i * 240, lambda s=scan: send(s, False))
        expected.extend([[int(key), "down"], [int(key), "up"]])

    def mouse(button, down):
        if down:
            if backend.user.GetForegroundWindow() != int(receiver.winId()):
                return
            center = receiver.mapToGlobal(receiver.rect().center())
            backend.user.SetCursorPos(center.x(), center.y())
        if not down and button not in buttons:
            return
        backend.mouse(button, down)
        buttons.add(button) if down else buttons.discard(button)

    QTimer.singleShot(1900, lambda: mouse("left", True))
    QTimer.singleShot(2020, lambda: mouse("left", False))
    QTimer.singleShot(2140, lambda: mouse("right", True))
    QTimer.singleShot(2260, lambda: mouse("right", False))

    def move():
        if backend.user.GetForegroundWindow() == int(receiver.winId()):
            result["moves_before_relative"] = receiver.moves
            backend.move(8, 4)
    QTimer.singleShot(2380, move)
    guard = InputGuard(backend)
    swing_timer = QTimer()
    swing_started = [None]
    fired = set()

    def swing_tick():
        now = time.monotonic()
        if backend.user.GetForegroundWindow() != int(receiver.winId()):
            guard.configure(False)
            return
        elapsed = now-swing_started[0]
        if elapsed >= .9 and "walk" not in fired:
            if guard.resume_movement(): fired.add("walk")
        gadget = elapsed >= 1.1 and "E" not in fired
        web_attack = elapsed >= 1.6 and "F" not in fired
        if gadget: fired.add("E")
        if web_attack: fired.add("F")
        swing_exit = elapsed >= 3.5 and "exit" not in fired
        if swing_exit: fired.add("exit")
        guard.publish_decision(Decision(swing=elapsed<.6 or 3.0<=elapsed<3.5,swing_exit=swing_exit,gadget=gadget,web_attack=web_attack,
                                       forward=2.0<=elapsed<2.2,backward=2.3<=elapsed<2.5),now)

    def start_swing():
        if backend.user.GetForegroundWindow() != int(receiver.winId()):
            return
        guard.configure(True, backend.foreground())
        swing_started[0] = time.monotonic()
        guard.start()
        swing_timer.timeout.connect(swing_tick)
        swing_timer.start(10)
    QTimer.singleShot(2700, start_swing)
    expected.extend([[int(Qt.Key.Key_Space), "down"], [int(Qt.Key.Key_Space), "up"],
                     [int(Qt.Key.Key_Shift), "down"], [int(Qt.Key.Key_Shift), "up"]])

    for key in (Qt.Key.Key_E,Qt.Key.Key_E,Qt.Key.Key_F,Qt.Key.Key_W,Qt.Key.Key_S):
        expected.extend([[int(key),"down"],[int(key),"up"]])

    for key,down in ((Qt.Key.Key_Space,True),(Qt.Key.Key_Space,False),(Qt.Key.Key_Shift,True),
                     (Qt.Key.Key_Space,True),(Qt.Key.Key_Space,False),(Qt.Key.Key_Shift,False)):
        expected.append([int(key),"down" if down else "up"])

    def finish():
        swing_timer.stop()
        guard.close()
        for scan in tuple(held):
            backend.send(scan, False)
        for button in tuple(buttons):
            backend.mouse(button, False)
        mouse_expected = [["LeftButton", "down"], ["LeftButton", "up"], ["RightButton", "down"], ["RightButton", "up"]]
        moved = receiver.moves > result.get("moves_before_relative", receiver.moves)
        result.update(accepted=backend.sent_count, events=receiver.events, mouse_events=receiver.mouse_events,
                      relative_motion_received=moved,
                      passed=receiver.events == expected and receiver.mouse_events == mouse_expected and moved, game_tested=False)
        if len(receiver.event_times) >= 20:
            result["double_e_gap_ms"] = round((receiver.event_times[18]-receiver.event_times[17])*1000,1)
            result["swing_space_ms"] = round((receiver.event_times[13] - receiver.event_times[12]) * 1000, 1)
            result["swing_gap_ms"] = round((receiver.event_times[14] - receiver.event_times[13]) * 1000, 1)
            result["passed"] = result["passed"] and result["swing_space_ms"] >= 90 and result["swing_gap_ms"] >= 35
        if len(receiver.events) == len(expected):
            result["swing_exit_space_ms"] = round((receiver.event_times[-2]-receiver.event_times[-3])*1000,1)
            result["swing_exit_order_verified"] = receiver.events[-6:] == expected[-6:]
        if backend.user.GetForegroundWindow() == int(receiver.winId()):
            backend.user.SetCursorPos(saved_cursor.x, saved_cursor.y)
        (ROOT / "artifacts").mkdir(exist_ok=True)
        (ROOT / "artifacts/input-check.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))
        receiver.close()
        app.exit(0 if result["passed"] else 1)

    QTimer.singleShot(6800, finish)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
