"""Gesture decisions independent of camera, model and keyboard APIs."""

from dataclasses import dataclass
from math import dist


def extended(hand, finger: int) -> bool:
    base = finger * 4 + 1
    wrist = (hand[0].x, hand[0].y, hand[0].z)
    tip = hand[base + 3]
    pip = hand[base + 1]
    return dist(wrist, (tip.x, tip.y, tip.z)) > 1.2 * dist(wrist, (pip.x, pip.y, pip.z))


def swing_hand(hand, shoulder_y: float = 0.65) -> bool:
    """Raised web pose or closed fist; thumb position is intentionally free."""
    fingers = [extended(hand, n) for n in range(1, 5)]
    web = fingers == [True, False, False, True]
    fist = not any(fingers)
    return (web or fist) and hand[0].y < shoulder_y


@dataclass
class Decision:
    swing: bool = False
    jump: bool = False
    calibrated: bool = False
    progress: float = 0.0


class Gestures:
    def __init__(self):
        self.reset()

    def reset(self):
        self.samples = []
        self.baseline = None
        self.last_y = None
        self.last_time = None
        self.last_pose = None
        self.last_jump = -10.0
        self.ready = True
        self.hand_since = None

    def update(self, hands, pose, now: float, threshold: float = 0.085):
        visible = bool(pose) and all(pose[i].visibility >= 0.6 for i in (0, 11, 12))
        shoulder_y = (pose[11].y + pose[12].y) / 2 if visible else 0.65
        raw_swing = any(swing_hand(h, shoulder_y) for h in hands)
        if not raw_swing:
            self.hand_since = None
        elif self.hand_since is None:
            self.hand_since = now
        swing = self.hand_since is not None and now - self.hand_since >= 0.10
        result = Decision(swing=swing, calibrated=self.baseline is not None)
        if not visible:
            self.last_y = self.last_time = None
            if self.baseline is None:
                self.samples.clear()
            return result
        # Shoulder span normalizes small head/body movements for camera distance.
        scale = max(abs(pose[11].x - pose[12].x), 0.10)
        y = (pose[0].y + shoulder_y) / 2
        if self.last_pose is not None and now - self.last_pose > 0.35:
            self.samples.clear()
            self.last_y = self.last_time = None
        self.last_pose = now
        if self.baseline is None:
            self.samples.append((now, y, scale))
            if max(s[1] for s in self.samples) - min(s[1] for s in self.samples) > scale * 0.10:
                self.samples = [(now, y, scale)]
            result.progress = min((now - self.samples[0][0]) / 1.2, 1.0)
            if result.progress >= 1:
                self.baseline = sum(s[1] for s in self.samples) / len(self.samples)
                self.samples.clear()
                result.calibrated = True
            self.last_y, self.last_time = y, now
            return result
        dt = now - self.last_time if self.last_time is not None else 0
        velocity = (self.last_y - y) / scale / dt if 0 < dt < 0.35 else 0
        displacement = (self.baseline - y) / scale
        if displacement < threshold * 0.4:
            self.ready = True
        if self.ready and displacement > threshold and velocity > 0.20 and now - self.last_jump > 0.65:
            result.jump = True
            self.last_jump, self.ready = now, False
        # Slowly follow natural posture drift without absorbing a jump.
        if abs(displacement) < threshold * 0.5:
            self.baseline += (y - self.baseline) * min(max(dt, 0) * 0.4, 0.05)
        self.last_y, self.last_time = y, now
        return result
