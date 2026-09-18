"""Time-based gesture state machines in mirrored camera coordinates."""

from dataclasses import dataclass, field
from math import dist, exp
from .calibration import finger_features, head_yaw


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
    look_x: float = 0.0
    debug: dict = field(default_factory=dict)
    mode: str = "Kalibrasyon"

    def description(self):
        actions = [name for name, active in (("SHIFT", self.swing), ("SPACE", self.jump),
                   ("RMB", self.aim), ("E", self.gadget), ("LMB", self.attack), ("CTRL", self.dodge)) if active]
        if abs(self.steer) > 0.25:
            actions.append("SOL" if self.steer < 0 else "SAĞ")
        return self.mode + (" · " + " + ".join(actions) if actions else "")


class Gestures:
    def __init__(self):
        self.profile = None
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
        self.head_yaw_zero = 0.0
        self.head_since = None
        self.head_side = 0

    def clear_transients(self):
        self.hand_since = self.aim_since = self.aim_bad_since = None
        self.aim = False
        self.opened.clear()
        self.tracks.clear()
        self.motion_tracks.clear()
        self.crouch_since = None
        self.last_y = self.last_time = None
        self.steer = self.pitch = 0.0
        self.head_since = None
        self.head_side = 0

    def apply_profile(self, profile):
        self.profile = profile
        self.reset()
        neutral = profile["neutral"]
        self.baseline, self.center, self.scale = neutral["y"], neutral["center"], neutral["scale"]
        self.yaw_zero, self.head_zero = neutral["yaw"], neutral["head"]
        self.head_yaw_zero = neutral["head_yaw"]

    def classify(self, hand):
        fallback = hand_shape(hand)
        if not self.profile:
            return fallback
        vector = finger_features(hand)
        scores = sorted((min(dist(vector, example) / 2 for example in examples), name)
                        for name, examples in self.profile["templates"].items())
        if scores[0][0] < 0.28 and (len(scores) < 2 or scores[1][0] - scores[0][0] > 0.06):
            return scores[0][1]
        return fallback

    def update(self, hands, pose, now, threshold=0.085, handedness=None):
        visible = bool(pose) and all(pose[i].visibility >= 0.6 for i in (0, 11, 12))
        result = Decision(calibrated=self.baseline is not None)
        shapes = [self.classify(h) for h in hands]
        result.debug = {"shapes": shapes, "fingers": [finger_features(h) for h in hands],
                        "aim_reason": "Gövde bekleniyor", "swing_reason": "Gövde bekleniyor",
                        "head_reason": "Yüz bekleniyor"}
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
        face_yaw = head_yaw(pose)
        y = (pose[0].y + shoulder_y) / 2
        dt = now - self.last_time if self.last_time is not None else 0.0
        if dt > 0.30:
            self.clear_transients()
            self.samples.clear()
            dt = 0
        if self.baseline is None:
            self.samples.append((now, y, center, yaw, head, scale, face_yaw))
            result.debug["aim_reason"] = result.debug["swing_reason"] = "Önce kalibrasyon"
            if (max(s[1] for s in self.samples) - min(s[1] for s in self.samples) > scale * 0.10
                    or max(s[2] for s in self.samples) - min(s[2] for s in self.samples) > scale * 0.10):
                self.samples = [self.samples[-1]]
            result.progress = min((now - self.samples[0][0]) / 1.2, 1.0)
            if result.progress >= 1:
                self.baseline, self.center, self.yaw_zero, self.head_zero, self.scale = (
                    sum(s[i] for s in self.samples) / len(self.samples) for i in range(1, 6))
                faces = [s[6] for s in self.samples if s[6] is not None]
                self.head_yaw_zero = sum(faces) / len(faces) if faces else 0
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
        waist_top = shoulder_y + scale * (self.profile["aim_min"] if self.profile else -0.20)
        waist_bottom = shoulder_y + scale * self.profile["aim_max"] if self.profile else max(hip_y, shoulder_y + scale * 1.8) + 0.2 * scale
        waist = len(hands) == 2 and all(waist_top < h[0].y < waist_bottom for h in hands)
        result.debug.update(aim_zone=[waist_top, waist_bottom], shoulder_y=shoulder_y)
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
        result.debug["aim_reason"] = ("AKTİF: sağ tık" if self.aim else "İki el bulunamadı" if len(hands) != 2
            else "Eller nişan bölgesi dışında" if not waist else "İki elde ağ şekli gerekli" if not both_web else "Ağ işaretini kısa süre sabit tut")

        upward = [h[0].y < shoulder_y - scale * 0.15 and h[8].y < h[0].y - scale * 0.12
                  and h[20].y < h[0].y - scale * 0.12 for h in hands]
        raw_swing = not self.aim and any(shape == "web" and up for shape, up in zip(shapes, upward))
        if raw_swing:
            if self.hand_since is None:
                self.hand_since = now
        else:
            self.hand_since = None
        result.swing = self.hand_since is not None and now - self.hand_since >= 0.08
        result.debug["swing_reason"] = ("AKTİF: Space → Shift" if result.swing else "Nişan modu aktif" if self.aim
            else "Ağ şekli bulunamadı" if "web" not in shapes else "Ağ elini yukarı kaldır / parmakları yukarı yönelt" if not raw_swing else "Ağ işaretini sabit tut")
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
        # Camera yaw follows the face, not lateral body movement. Dwell and a
        # separate release threshold reject neutral jitter and brief glances.
        normalized = 0.0
        if face_yaw is not None:
            delta = face_yaw - self.head_yaw_zero
            left = self.profile["head_left"] if self.profile else -0.4
            right = self.profile["head_right"] if self.profile else 0.4
            normalized = -abs(delta / left) if delta * left > 0 else abs(delta / right)
        gate = self.profile["head_gate"] if self.profile else 0.55
        side = -1 if normalized < 0 else 1
        magnitude = abs(normalized)
        if face_yaw is None or magnitude < gate * 0.6:
            self.head_since = None
            self.head_side = 0
        elif self.head_side != side and magnitude < gate:
            self.head_since = None
            self.head_side = 0
        elif magnitude >= gate and (self.head_side != side or self.head_since is None):
            self.head_side, self.head_since = side, now
        if self.head_since is not None and side == self.head_side and now - self.head_since >= 0.18:
            result.look_x = self.head_side * min(1.0, max(0.15, (magnitude - gate * 0.6) / (1 - gate * 0.6)))
        result.look_y = self.pitch if self.aim and abs(pitch) > 0.25 else 0
        result.debug.update(head_value=normalized, head_gate=gate,
            head_reason="Baş açısı görünmüyor" if face_yaw is None else "SOLA" if result.look_x < 0 else "SAĞA" if result.look_x > 0 else "Nötr / belirgin dönüş bekleniyor")
        result.mode = "Nişan" if result.aim else "Swing" if result.swing else "Eğilme" if self.crouch_since is not None else "Serbest"
        self.last_y, self.last_time = y, now
        return result
