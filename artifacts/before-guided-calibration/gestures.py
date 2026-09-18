"""Time-based gesture state machines in mirrored camera coordinates."""

from dataclasses import dataclass
from math import dist, exp


def xyz(p):
    return p.x, p.y, getattr(p, "z", 0.0)


def extended(hand, finger: int) -> bool:
    base = finger * 4 + 1
    return dist(xyz(hand[0]), xyz(hand[base + 3])) > 1.2 * dist(xyz(hand[0]), xyz(hand[base + 1]))


def hand_shape(hand):
    fingers = [extended(hand, n) for n in range(1, 5)]
    if fingers == [True, False, False, True]:
        return "web"
    if not any(fingers):
        return "fist"
    if all(fingers):
        return "open"
    return "other"


def swing_hand(hand, shoulder_y=0.65, scale=0.3):
    return (hand_shape(hand) == "web" and hand[0].y < shoulder_y - scale * 0.15
            and hand[8].y < hand[0].y - scale * 0.12
            and hand[20].y < hand[0].y - scale * 0.12)


def deadzone(value, zone):
    if abs(value) <= zone:
        return 0.0
    return max(-1.0, min(1.0, (value - (zone if value > 0 else -zone)) / (1 - zone)))


@dataclass
class Decision:
    swing: bool = False
    jump: bool = False
    calibrated: bool = False
    progress: float = 0.0
    aim: bool = False
    gadget: bool = False
    attack: bool = False
    dodge: bool = False
    steer: float = 0.0
    look_y: float = 0.0
    mode: str = "Kalibrasyon"

    def description(self):
        actions = [name for name, active in (("SHIFT", self.swing), ("SPACE", self.jump),
                   ("RMB", self.aim), ("E", self.gadget), ("LMB", self.attack), ("CTRL", self.dodge)) if active]
        if abs(self.steer) > 0.25:
            actions.append("SOL" if self.steer < 0 else "SAĞ")
        return self.mode + (" · " + " + ".join(actions) if actions else "")


class Gestures:
    def __init__(self):
        self.reset()

    def reset(self):
        self.samples = []
        self.baseline = None
        self.center = self.yaw_zero = self.head_zero = 0.0
        self.scale = 0.3
        self.last_y = self.last_time = None
        self.last_jump = self.last_dodge = self.last_attack = self.last_gadget = -10.0
        self.jump_ready = True
        self.crouch_since = None
        self.jump_block_until = 0.0
        self.attack_block_until = 0.0
        self.hand_since = self.aim_since = None
        self.aim = False
        self.aim_bad_since = None
        self.opened = {}
        self.tracks = {}
        self.motion_tracks = {}
        self.steer = self.pitch = 0.0

    def clear_transients(self):
        self.hand_since = self.aim_since = self.aim_bad_since = None
        self.aim = False
        self.opened.clear()
        self.tracks.clear()
        self.motion_tracks.clear()
        self.crouch_since = None
        self.last_y = self.last_time = None
        self.steer = self.pitch = 0.0

    def update(self, hands, pose, now, threshold=0.085, handedness=None):
        visible = bool(pose) and all(pose[i].visibility >= 0.6 for i in (0, 11, 12))
        result = Decision(calibrated=self.baseline is not None)
        if not visible:
            self.clear_transients()
            self.samples.clear()
            result.mode = "Gövde görünmüyor"
            return result
        shoulder_y = (pose[11].y + pose[12].y) / 2
        center = (pose[11].x + pose[12].x) / 2
        scale = max(abs(pose[11].x - pose[12].x), 0.10)
        yaw = (getattr(pose[11], "z", 0) - getattr(pose[12], "z", 0)) / scale
        head = (pose[0].y - shoulder_y) / scale
        y = (pose[0].y + shoulder_y) / 2
        dt = now - self.last_time if self.last_time is not None else 0.0
        if dt > 0.30:
            self.clear_transients()
            self.samples.clear()
            dt = 0
        if self.baseline is None:
            self.samples.append((now, y, center, yaw, head, scale))
            if (max(s[1] for s in self.samples) - min(s[1] for s in self.samples) > scale * 0.10
                    or max(s[2] for s in self.samples) - min(s[2] for s in self.samples) > scale * 0.10):
                self.samples = [self.samples[-1]]
            result.progress = min((now - self.samples[0][0]) / 1.2, 1.0)
            if result.progress >= 1:
                self.baseline, self.center, self.yaw_zero, self.head_zero, self.scale = (
                    sum(s[i] for s in self.samples) / len(self.samples) for i in range(1, 6))
                self.samples.clear()
                result.calibrated = True
            self.last_y, self.last_time = y, now
            return result

        # Calibrated width keeps a torso turn from amplifying motion thresholds.
        observed_scale = scale
        scale = self.scale
        velocity = (self.last_y - y) / scale / dt if 0 < dt <= 0.30 else 0.0
        displacement = (self.baseline - y) / scale
        if displacement < -0.20 and self.crouch_since is None:
            self.crouch_since = now
        if self.crouch_since is not None:
            self.jump_block_until = now + 0.4
            if displacement > -0.06:
                if 0.12 <= now - self.crouch_since <= 2.5 and velocity > 0.15 and now - self.last_dodge > 0.65:
                    result.dodge = True
                    self.last_dodge = now
                self.crouch_since = None
        if abs(displacement) < threshold * 0.4:
            self.jump_ready = True
        if (self.jump_ready and now > self.jump_block_until and displacement > threshold
                and velocity > 0.65 and now - self.last_jump > 0.65):
            result.jump = True
            self.jump_ready = False
            self.last_jump = now

        hips_visible = all(pose[i].visibility >= 0.6 for i in (23, 24))
        hip_y = (pose[23].y + pose[24].y) / 2 if hips_visible else shoulder_y + scale * 1.6
        waist_bottom = max(hip_y, shoulder_y + scale) + 0.2 * scale
        waist = len(hands) == 2 and all(shoulder_y + scale * 0.25 < h[0].y < waist_bottom for h in hands)
        shapes = [hand_shape(h) for h in hands]
        both_web = waist and shapes == ["web", "web"]
        if both_web:
            if self.aim_since is None:
                self.aim_since = now
            if now - self.aim_since >= 0.12:
                self.aim = True
        else:
            self.aim_since = None
        if self.aim:
            if not waist:
                if self.aim_bad_since is None:
                    self.aim_bad_since = now
                if len(hands) != 2 or now - self.aim_bad_since >= 0.12:
                    self.aim = False
                    self.opened.clear()
            else:
                self.aim_bad_since = None
        result.aim = self.aim

        raw_swing = not self.aim and any(swing_hand(h, shoulder_y, scale) for h in hands)
        if raw_swing:
            if self.hand_since is None:
                self.hand_since = now
        else:
            self.hand_since = None
        result.swing = self.hand_since is not None and now - self.hand_since >= 0.08
        if result.aim or result.swing:
            self.attack_block_until = now + 0.2

        # Hand labels keep history attached when detector output order changes.
        tracks = {}
        for i, (hand, shape) in enumerate(zip(hands, shapes)):
            identity = handedness[i] if handedness and i < len(handedness) else ("left" if hand[0].x < center else "right")
            if identity in tracks:
                continue
            previous = self.tracks.get(identity)
            palm = max(dist(xyz(hand[0]), xyz(hand[9])), 0.02) / observed_scale
            position = (hand[0].x - center, hand[0].y - shoulder_y)
            ready = previous[4] if previous else True
            idle_since = previous[5] if previous else None
            history = self.motion_tracks.get(identity, []) if previous and previous[3] == shape else []
            history = [sample for sample in history if now - sample[0] <= 0.25]
            history.append((now, position, palm))
            self.motion_tracks[identity] = history
            if self.aim and waist:
                if shape == "open" and identity not in self.opened:
                    self.opened[identity] = now
                if shape in ("web", "fist") and identity in self.opened:
                    opened = self.opened.pop(identity)
                    if 0.05 <= now - opened <= 1.2 and now - self.last_gadget > 0.35:
                        result.gadget = True
                        self.last_gadget = now
            else:
                self.opened.pop(identity, None)
            if previous and 0 < now - previous[0] <= 0.25:
                elapsed = now - previous[0]
                movement = dist(position, previous[1]) / scale
                expansion = max(0.0, palm / previous[2] - 1)
                speed = movement / elapsed
                if speed < 0.55 and expansion < 0.08:
                    idle_since = now if idle_since is None else idle_since
                    if now - idle_since >= 0.12:
                        ready = True
                else:
                    idle_since = None
                window_dt = now - history[0][0]
                travel = dist(position, history[0][1]) / scale
                growth = max(0.0, palm / history[0][2] - 1)
                if (shape == previous[3] == "fist" and ready and not self.aim and not result.swing
                        and now >= self.attack_block_until
                        and window_dt > 0
                        and (travel > 0.22 and travel / window_dt > 1.8 or growth > 0.22 and growth / window_dt > 1.8)
                        and now - self.last_attack > 0.30):
                    result.attack = True
                    self.last_attack = now
                    ready = False
            tracks[identity] = (now, position, palm, shape, ready, idle_since)
        self.tracks = tracks
        self.motion_tracks = {key: self.motion_tracks[key] for key in tracks}

        lean = (center - self.center) / scale
        turn = (yaw - self.yaw_zero) * 0.35
        target = deadzone(lean * 2.0 + turn, 0.16)
        alpha = 1 - exp(-max(dt, 0) / 0.07)
        self.steer += alpha * (target - self.steer)
        if abs(target) < 0.001 and abs(self.steer) < 0.04:
            self.steer = 0
        pitch = deadzone((head - self.head_zero) * 2.5, 0.12) if self.aim else 0.0
        self.pitch += alpha * (pitch - self.pitch)
        result.steer = self.steer
        result.look_y = self.pitch if self.aim else 0
        result.mode = "Nişan" if result.aim else "Swing" if result.swing else "Eğilme" if self.crouch_since is not None else "Serbest"
        self.last_y, self.last_time = y, now
        return result
