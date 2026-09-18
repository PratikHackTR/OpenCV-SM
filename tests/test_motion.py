import unittest

from test_controls import pose, hand, FakeInput
from webmotion.gestures import Gestures, Decision
from webmotion.keyboard import InputGuard

WEB = [True, False, False, True]
OPEN = [True] * 4
FIST = [False] * 4


def body(y=0.4, x=0.5):
    p = pose(y)
    for landmark in p:
        landmark.x += x - 0.5
        landmark.z = 0.0
    p[23].y = p[24].y = y + 0.55
    return p


def palm(shape=WEB, x=0.3, y=0.75, size=1):
    h = hand(shape, y)
    for p in h:
        p.x += x - 0.5
        p.y = y + (p.y - y) * size
    h[9].y = y - 0.06 * size
    return h


def calibrated(fps=30):
    g = Gestures()
    for i in range(round(1.3 * fps) + 1):
        g.update([], body(), i / fps)
    assert g.baseline is not None
    return g


class MotionTests(unittest.TestCase):
    def test_swing_requires_upward_web_and_not_fist(self):
        for shape, y, expected in ((WEB, 0.3, True), (WEB, 0.75, False), (FIST, 0.3, False)):
            g = calibrated()
            h = palm(shape, y=y)
            g.update([h], body(), 1.4)
            self.assertEqual(g.update([h], body(), 1.51).swing, expected)
        g = calibrated()
        h = palm(WEB, y=0.3)
        for p in h:
            p.y = 0.6 - p.y  # Raised wrist may rotate without dropping its web gesture.
        g.update([h], body(), 1.4)
        self.assertTrue(g.update([h], body(), 1.51).swing)

    def aim(self):
        g = calibrated()
        hands = [palm([True, True, False, False], x=.5, y=.6)]
        g.update(hands, body(), 1.4, handedness=["L"])
        result = g.update(hands, body(), 1.6, handedness=["L"])
        self.assertTrue(result.aim)
        self.assertFalse(result.swing)
        return g, hands


    def test_no_gadget_from_open_hand_outside_aim(self):
        g = calibrated()
        g.update([palm(OPEN)], body(), 1.4)
        self.assertFalse(g.update([palm(FIST)], body(), 1.5).gadget)

    def test_punch_at_multiple_inference_rates(self):
        for fps in (5, 10, 14, 30, 60):
            with self.subTest(fps=fps):
                g = calibrated(fps)
                count = 0
                for i in range(round(fps * 0.5)):
                    t = i / fps
                    x = 0.25 + min(t, 0.20) * 1.1
                    r = g.update([palm(FIST, x=x)], body(), 1.4 + t, handedness=["L"])
                    count += r.attack
                self.assertEqual(count, 1)

    def test_stationary_fist_never_attacks(self):
        g = calibrated()
        for i in range(30):
            self.assertFalse(g.update([palm(FIST)], body(), 1.4 + i / 30).attack)

    def test_whole_body_translation_is_not_punch(self):
        g = calibrated()
        g.update([palm(FIST, x=0.25)], body(), 1.4)
        self.assertFalse(g.update([palm(FIST, x=0.40)], body(x=0.65), 1.5).attack)

    def test_forward_punch_uses_palm_expansion(self):
        g = calibrated()
        g.update([palm(FIST)], body(), 1.4)
        self.assertTrue(g.update([palm(FIST, size=1.35)], body(), 1.5).attack)

    def test_shoulder_rotation_does_not_steer(self):
        g = calibrated()
        p = body()
        p[11].z, p[12].z = 0.25, -0.25
        self.assertEqual(g.update([], p, 1.4).steer, 0)

    def test_dodge_overshoot_does_not_also_jump(self):
        g = calibrated()
        g.update([], body(0.5), 1.4)
        g.update([], body(0.5), 1.55)
        result = g.update([], body(0.35), 1.65)
        self.assertTrue(result.dodge)
        self.assertFalse(result.jump)

    def test_crouch_return_dodges_without_jump(self):
        for fps in (10, 14, 30, 60):
            with self.subTest(fps=fps):
                g = calibrated(fps)
                results = []
                for i in range(round(fps * 1.2)):
                    t = i / fps
                    offset = min(t / 0.2, 1) * 0.10 if t < 0.4 else max(0, 1 - (t - 0.4) / 0.2) * 0.10
                    results.append(g.update([], body(0.4 + offset), 1.4 + t))
                self.assertEqual(sum(r.dodge for r in results), 1)
                self.assertEqual(sum(r.jump for r in results), 0)

    def test_sudden_rise_jumps_slow_rise_does_not(self):
        g = calibrated()
        self.assertTrue(g.update([], body(0.36), 1.4).jump)
        g = calibrated()
        for i in range(25):
            self.assertFalse(g.update([], body(0.4 - i * 0.002), 1.4 + i * 0.1).jump)

    def test_steering_deadzone_and_direction(self):
        g = calibrated()
        r = g.update([], body(x=0.505), 1.4)
        self.assertEqual(r.steer, 0)
        r = g.update([], body(x=0.62), 1.5)
        self.assertEqual(r.steer, 0)
        r = g.update([], body(x=0.38), 1.65)
        self.assertEqual(r.steer, 0)
        r = g.update([], [], 1.7)
        self.assertEqual(r.steer, 0)

    def test_tracking_gap_cancels_aim_and_punch_history(self):
        g, hands = self.aim()
        r = g.update([palm(FIST, x=0.8)], body(), 2.0)
        self.assertFalse(r.aim)
        self.assertFalse(r.attack)






    def test_body_translation_with_arm_does_not_steer(self):
        g = calibrated()
        g.update([palm(x=.3, y=.3)], body(), 1.4, handedness=["L"])
        g.update([palm(x=.3, y=.3)], body(), 1.5, handedness=["L"])
        for i in range(8):
            r = g.update([palm(x=.5, y=.3)], body(x=.7), 1.6+i*.05, handedness=["L"])
            self.assertEqual(r.steer, 0)



class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeInput()
        self.guard = InputGuard(self.backend)
        self.guard.configure(True)

    def publish(self, decision, now=1):
        self.guard.publish_decision(decision, now)
        self.guard.step(now + 0.01)

    def test_aim_blocks_attack_swing_but_allows_strafing(self):
        self.publish(Decision(aim=True, attack=True, steer=1, gadget=True))
        self.assertEqual(self.backend.mouse_events, [("right", True)])
        self.assertEqual(self.backend.events, [(self.guard.E, True), (self.guard.D, True)])

    def test_attack_dodge_gadget_pulses_release(self):
        self.publish(Decision(attack=True, dodge=True))
        self.guard.step(1.12)
        self.assertEqual(self.backend.mouse_events, [("left", True), ("left", False)])
        self.assertEqual(self.backend.events, [(self.guard.CTRL, True), (self.guard.CTRL, False)])

    def test_focus_loss_releases_all_inputs_and_motion(self):
        self.publish(Decision(swing=True, steer=1, attack=True, dodge=True))
        self.guard.step(1.02)
        moves = len(self.backend.moves)
        self.backend.process = "notepad.exe"
        self.guard.step(1.03)
        self.assertFalse(self.guard.held)
        self.assertFalse(self.guard.mouse_held)
        self.assertEqual(len(self.backend.moves), moves)

    def test_stale_camera_releases_aim(self):
        self.publish(Decision(aim=True))
        self.guard.step(1.31)
        self.assertEqual(self.backend.mouse_events, [("right", True), ("right", False)])

    def test_f8_releases_buttons_and_cancels_movement(self):
        self.publish(Decision(aim=True, steer=1, gadget=True))
        self.backend.panic = True
        self.guard.step(1.02)
        self.assertFalse(self.guard.enabled)
        self.assertFalse(self.guard.held)
        self.assertFalse(self.guard.mouse_held)
        self.assertEqual(self.backend.moves, [])

    def test_background_attack_and_gadget_do_not_replay(self):
        self.backend.process = "notepad.exe"
        self.publish(Decision(attack=True, gadget=True, aim=True))
        self.backend.process = "spider-man.exe"
        self.guard.step(1.02)
        self.assertNotIn(("left", True), self.backend.mouse_events)
        self.assertNotIn((self.guard.E, True), self.backend.events)

    def test_direction_flip_releases_before_press(self):
        self.publish(Decision(steer=-1))
        self.publish(Decision(steer=1), 1.1)
        self.assertEqual([event for event in self.backend.events if event[0] in (self.guard.A, self.guard.D)], [(self.guard.A,True),(self.guard.A,False),(self.guard.D,True)])





