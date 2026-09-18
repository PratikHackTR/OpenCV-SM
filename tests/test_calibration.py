import json
from pathlib import Path
import tempfile
import unittest

from test_controls import FakeInput
from test_motion import body, palm, calibrated, WEB, OPEN, FIST
from webmotion.calibration import GuidedCalibration, observe, load_profile, save_profile
from webmotion.gestures import Decision, Gestures
from webmotion.keyboard import InputGuard


def face(yaw=0.0):
    points = body()
    points[7].x, points[8].x = 0.4, 0.6
    points[0].x = 0.5 + yaw * 0.2
    return points


class SwingSequenceTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeInput()
        self.guard = InputGuard(self.backend)
        self.guard.configure(True)

    def test_one_jump_then_shift_no_repeat_while_held(self):
        for i in range(100):
            now = 1 + i / 100
            self.guard.publish_decision(Decision(swing=True), now)
            self.guard.step(now)
        self.assertEqual(self.backend.events, [(0x39, True), (0x39, False), (0x2A, True)])
        self.guard.publish_decision(Decision(), 2)
        self.guard.step(2)
        self.assertEqual(self.backend.events[-1], (0x2A, False))
        self.guard.publish_decision(Decision(swing=True), 2.1)
        self.guard.step(2.1)
        self.assertEqual(self.backend.events[-1], (0x39, True))

    def test_cancel_before_shift_never_sends_delayed_shift(self):
        self.guard.publish_decision(Decision(swing=True), 1)
        self.guard.step(1)
        self.guard.publish_decision(Decision(), 1.05)
        self.guard.step(1.05)
        self.guard.step(1.2)
        self.assertEqual(self.backend.events, [(0x39, True), (0x39, False)])

    def test_focus_loss_cancels_swing_sequence(self):
        self.guard.publish_decision(Decision(swing=True), 1)
        self.guard.step(1)
        self.backend.process = "notepad.exe"
        self.guard.step(1.12)
        self.guard.step(1.2)
        self.assertEqual(self.backend.events, [(0x39, True), (0x39, False)])


class HeadTests(unittest.TestCase):
    def setup_head(self):
        g = Gestures()
        for i in range(40):
            g.update([], face(), i / 30)
        return g

    def test_neutral_jitter_and_short_glance_do_not_turn(self):
        g = self.setup_head()
        for i in range(10):
            self.assertEqual(g.update([], face((-1) ** i * 0.05), 1.4 + i * 0.05).look_x, 0)
        self.assertEqual(g.update([], face(0.4), 1.9).look_x, 0)
        self.assertEqual(g.update([], face(), 2).look_x, 0)

    def test_head_turns_no_longer_control_camera(self):
        g = self.setup_head()
        g.update([], face(0.4), 1.4)
        self.assertEqual(g.update([], face(0.4), 1.6).look_x, 0)
        self.assertEqual(g.update([], face(), 1.7).look_x, 0)
        g.update([], face(-0.4), 1.8)
        self.assertEqual(g.update([], face(-0.4), 2.0).look_x, 0)
        self.assertEqual(g.update([], [], 2.1).look_x, 0)

    def test_body_steering_does_not_move_camera(self):
        b = FakeInput()
        guard = InputGuard(b)
        guard.configure(True)
        for i in range(10):
            now = 1 + i * 0.01
            guard.publish_decision(Decision(steer=1), now)
            guard.step(now)
        self.assertEqual(b.moves, [])
        self.assertIn((guard.D, True), b.events)


class CalibrationTests(unittest.TestCase):
    def sample(self, session, obs, now):
        session.begin_sample(now)
        for i in range(15):
            session.update(obs, now + 2 + i * 0.1)

    def complete(self):
        session = GuidedCalibration()
        session.start()
        observations = [observe([], face()),
                        observe([palm(WEB, y=0.3)], face()),
                        observe([palm(WEB, x=0.3, y=0.60), palm(WEB, x=0.7, y=0.60)], face()),
                        observe([palm(OPEN, x=0.3), palm(OPEN, x=0.7)], face()),
                        observe([palm(FIST, x=0.3), palm(FIST, x=0.7)], face())]
        for i, obs in enumerate(observations):
            self.sample(session, obs, i * 5)
        return session

    def test_five_steps_produce_compatible_profile_with_two_finger_aim(self):
        session = self.complete()
        self.assertFalse(session.active)
        self.assertIsNotNone(session.profile)
        g = Gestures()
        g.apply_profile(session.profile)
        hands = [palm([True,True,False,False], x=.5, y=.60)]
        g.update(hands, face(), 40)
        self.assertTrue(g.update(hands, face(), 40.2).aim)
        self.assertEqual(g.classify(palm(OPEN)), "open")
        self.assertEqual(g.classify(palm(FIST)), "fist")

    def test_missing_two_hands_does_not_advance(self):
        session = GuidedCalibration()
        session.start()
        session.index = 2
        self.sample(session, observe([palm(WEB)], face()), 1)
        self.assertEqual(session.index, 2)
        self.assertIn("İki el", session.message)

    def test_missing_pose_resets_progress(self):
        session = GuidedCalibration()
        session.start()
        session.begin_sample(0)
        session.update(observe([], face()), 2)
        session.update(observe([], face()), 2.5)
        session.update(None, 2.6)
        self.assertEqual(session.progress, 0)
        self.assertEqual(len(session.samples), 0)

    def test_neutral_does_not_require_head_angle(self):
        session = GuidedCalibration()
        session.start()
        obs = observe([], face())
        obs["head_yaw"] = None
        self.sample(session, obs, 0)
        self.assertEqual(session.index, 1)

    def test_profile_round_trip_and_corruption(self):
        profile = self.complete().profile
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "profile.json"
            save_profile(path, profile)
            self.assertEqual(load_profile(path), profile)
            path.write_text("broken", encoding="utf-8")
            self.assertIsNone(load_profile(path))

    def test_malformed_templates_are_ignored(self):
        profile = self.complete().profile
        profile["templates"] = {}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "profile.json"
            save_profile(path, profile)
            self.assertIsNone(load_profile(path))

    def test_unstable_body_sample_does_not_advance(self):
        session = GuidedCalibration()
        session.start()
        session.begin_sample(0)
        for i in range(14):
            session.update(observe([], body(x=.5 + (-1) ** i * .1)), 2 + i * .1)
        self.assertEqual(session.index, 0)
        self.assertIsNone(session.profile)

    def test_debug_explains_new_aim_gesture(self):
        g = calibrated()
        r = g.update([palm(WEB)], body(), 1.4)
        self.assertIn("kadraj", r.debug["aim_reason"])
