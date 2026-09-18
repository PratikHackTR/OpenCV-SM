import unittest
from types import SimpleNamespace as Point

from webmotion.gestures import Gestures, Decision, swing_hand
from webmotion.keyboard import InputGuard


def pose(y=0.4, visible=1.0):
    points = [Point(x=0.5, y=y, visibility=visible) for _ in range(33)]
    points[11] = Point(x=0.35, y=y + 0.2, visibility=visible)
    points[12] = Point(x=0.65, y=y + 0.2, visibility=visible)
    return points


def hand(fingers, wrist_y=0.4):
    points = [Point(x=0.5, y=wrist_y, z=0.0) for _ in range(21)]
    for finger, straight in enumerate(fingers, 1):
        base = finger * 4 + 1
        points[base + 1].y = wrist_y - 0.1
        points[base + 3].y = wrist_y - (0.2 if straight else 0.06)
    return points


class GestureTests(unittest.TestCase):
    def test_web_fist_open_lowered(self):
        self.assertTrue(swing_hand(hand([True, False, False, True])))
        self.assertFalse(swing_hand(hand([False] * 4)))
        self.assertFalse(swing_hand(hand([True] * 4)))
        self.assertFalse(swing_hand(hand([False] * 4, 0.8)))

    def test_debounce_and_tracking_loss(self):
        g = self.calibrated()
        web = [hand([True, False, False, True])]
        self.assertFalse(g.update(web, pose(), 1.4).swing)
        self.assertTrue(g.update(web, pose(), 1.5).swing)
        self.assertTrue(g.update([], [], 1.52).swing)
        g.update([], [], 1.7)
        self.assertFalse(g.update([], [], 1.9).swing)

    def calibrated(self):
        g = Gestures()
        for i in range(40):
            g.update([], pose(), i / 30)
        self.assertIsNotNone(g.baseline)
        return g

    def test_jump_once_and_rearm(self):
        g = self.calibrated()
        self.assertTrue(g.update([], pose(0.36), 1.4).jump)
        self.assertFalse(g.update([], pose(0.34), 1.45).jump)
        self.assertFalse(g.update([], pose(0.36), 1.9).jump)
        g.update([], pose(), 2.0)
        self.assertTrue(g.update([], pose(0.36), 2.1).jump)

    def test_pose_reappearance_does_not_jump(self):
        g = self.calibrated()
        g.update([], [], 1.4)
        self.assertFalse(g.update([], pose(0.2), 2.0).jump)

    def test_uncertain_pose_and_reset(self):
        g = Gestures()
        for i in range(60):
            g.update([], pose(visible=0.2), i / 30)
        self.assertIsNone(g.baseline)
        g = self.calibrated()
        g.reset()
        self.assertIsNone(g.baseline)


class FakeInput:
    def __init__(self):
        self.events = []
        self.process = "spider-man.exe"
        self.panic = False
        self.mouse_events = []
        self.moves = []

    def foreground(self):
        return self.process

    def emergency(self):
        return self.panic

    def send(self, code, down):
        self.events.append((code, down))

    def mouse(self, button, down):
        self.mouse_events.append((button, down))

    def move(self, dx, dy):
        self.moves.append((dx, dy))


class InputTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeInput()
        self.guard = InputGuard(self.backend)
        self.guard.configure(True)

    def test_focus_loss_releases_shift(self):
        self.guard.publish(True, False, 1)
        self.guard.step(1.01)
        self.guard.step(1.12)
        self.guard.step(1.18)
        self.backend.process = "notepad.exe"
        self.guard.step(1.19)
        self.assertEqual(self.backend.events, [(0x39, True), (0x39, False), (0x2A, True), (0x2A, False)])

    def test_watchdog_releases_stalled_capture(self):
        self.guard.publish(True, False, 1)
        self.guard.step(1.01)
        self.guard.step(1.31)
        self.assertFalse(self.guard.held)

    def test_no_repeat_and_space_pulse(self):
        self.guard.publish(True, True, 1)
        self.guard.step(1.01)
        self.guard.step(1.02)
        self.guard.step(1.12)
        self.guard.step(1.18)
        self.assertEqual(self.backend.events, [(0x39, True), (0x39, False), (0x2A, True)])

    def test_emergency_disarms(self):
        self.guard.publish(True, False, 1)
        self.guard.step(1.01)
        self.backend.panic = True
        self.guard.step(1.02)
        self.assertFalse(self.guard.enabled)
        self.assertFalse(self.guard.held)

    def test_background_jump_is_discarded(self):
        self.backend.process = "notepad.exe"
        self.guard.publish(False, True, 1)
        self.guard.step(1.01)
        self.backend.process = "spider-man.exe"
        self.guard.step(1.02)
        self.assertEqual(self.backend.events, [])

    def test_disabled_never_sends(self):
        self.guard.configure(False)
        self.guard.publish(True, True, 1)
        self.guard.step(1.01)
        self.assertEqual(self.backend.events, [])

    def test_manual_space_without_camera(self):
        self.guard.schedule_test("spider-man.exe", now=1)
        self.guard.step(5.9)
        self.assertEqual(self.backend.events, [])
        self.guard.step(6)
        self.guard.step(6.16)
        self.guard.step(7)
        self.assertEqual(self.backend.events, [(0x39, True), (0x39, False)])
        self.assertIn("SPACE testi gönderildi", self.guard.status)

    def test_manual_space_requires_foreground(self):
        self.guard.schedule_test("spider-man.exe", now=1)
        self.backend.process = "notepad.exe"
        self.guard.step(6)
        self.assertEqual(self.backend.events, [])
        self.assertIsNone(self.guard.test_due)

    def test_emergency_cancels_pending_test(self):
        self.guard.schedule_test("spider-man.exe", now=1)
        self.backend.panic = True
        self.guard.step(3)
        self.backend.panic = False
        self.guard.step(6)
        self.assertEqual(self.backend.events, [])

    def test_integrity_mismatch_prevents_false_success(self):
        def blocked():
            raise PermissionError("UIPI")
        self.backend.check_access = blocked
        self.guard.publish(True, True, 1)
        with self.assertRaises(PermissionError):
            self.guard.step(1.01)
        self.assertEqual(self.backend.events, [])
        self.assertFalse(self.guard.held)


if __name__ == "__main__":
    unittest.main()
