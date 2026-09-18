"""Time-based gesture state machines in mirrored camera coordinates."""

from dataclasses import dataclass, field
from math import dist
from types import SimpleNamespace
from .calibration import finger_features, head_yaw
from .hand_controls import HandControls


def xyz(p):
    return p.x, p.y, getattr(p, "z", 0.0)


def extended(hand, finger: int) -> bool:
    base = finger * 4 + 1
    return dist(xyz(hand[0]), xyz(hand[base + 3])) > 1.2 * dist(xyz(hand[0]), xyz(hand[base + 1]))


def hand_shape(hand):
    fingers = [extended(hand, n) for n in range(1, 5)]
    if fingers == [True, False, False, True]:
        return "web"
    if fingers == [True, True, False, False]:
        return "two"
    if fingers == [True, False, False, False]:
        return "point"
    if not any(fingers):
        return "fist"
    if all(fingers):
        return "open"
    return "other"


def swing_hand(hand, shoulder_y=0.65, scale=0.3):
    return (hand_shape(hand) == "web" and hand[0].y < shoulder_y - scale * 0.15
            and hand[8].y < hand[0].y - scale * 0.12
            and hand[20].y < hand[0].y - scale * 0.12)


@dataclass
class Decision:
    swing: bool = False
    swing_cast: bool = False
    swing_exit: bool = False
    web_attack: bool = False
    mouse_dx: float = 0.0
    mouse_dy: float = 0.0
    mouse_session: str = ""
    jump: bool = False
    calibrated: bool = False
    progress: float = 0.0
    aim: bool = False
    gadget: bool = False
    attack: bool = False
    dodge: bool = False
    steer: float = 0.0
    forward: bool = False
    backward: bool = False
    look_y: float = 0.0
    look_x: float = 0.0
    debug: dict = field(default_factory=dict)
    mode: str = "Kalibrasyon"

    def description(self):
        actions = [name for name, active in (("SHIFT", self.swing), ("SPACE", self.jump),
                   ("RMB", self.aim), ("F", self.web_attack), ("E", self.gadget), ("LMB", self.attack), ("CTRL", self.dodge)) if active]
        if abs(self.steer) > 0.25:
            actions.append("SOL" if self.steer < 0 else "SAĞ")
        if self.forward:
            actions.append("W")
        if self.backward:
            actions.append("S")
        return self.mode + (" · " + " + ".join(actions) if actions else "")


class Gestures:
    def __init__(self):
        self.profile = None
        self.tolerance = 0.018
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
        self.shoulder_baseline = None
        self.dodge_ready = True
        self.dodge_neutral_since = None
        self.body_block_until = 0
        self.jump_block_until = 0.0
        self.attack_block_until = 0.0
        self.hand_since = self.aim_since = None
        self.aim = False
        self.aim_bad_since = None
        self.opened = {}
        self.tracks = {}
        self.motion_tracks = {}
        self.steer = 0.0
        self.head_yaw_zero = 0.0
        self.head_since = None
        self.head_side = 0
        self.hand_controls = HandControls()

    def clear_transients(self):
        self.hand_since = self.aim_since = self.aim_bad_since = None
        self.aim = False
        self.opened.clear()
        self.tracks.clear()
        self.motion_tracks.clear()
        self.crouch_since = None
        self.last_y = self.last_time = None
        self.steer = 0.0
        self.head_since = None
        self.head_side = 0
        self.hand_controls = HandControls()

    def apply_profile(self, profile):
        self.profile = profile
        self.reset()
        neutral = profile["neutral"]
        self.baseline, self.center, self.scale = neutral["y"], neutral["center"], neutral["scale"]
        self.yaw_zero, self.head_zero = neutral["yaw"], neutral["head"]
        self.head_yaw_zero = neutral["head_yaw"]

    def classify(self, hand):
        return hand_shape(hand)

    def update(self, hands, pose, now, threshold=0.085, handedness=None):
        visible = bool(pose) and all(pose[i].visibility >= 0.6 for i in (0, 11, 12))
        result = Decision(calibrated=self.baseline is not None)
        shapes = [self.classify(h) for h in hands]
        result.debug = {"shapes": shapes, "fingers": [finger_features(h) for h in hands],
                        "aim_reason": "Gövde bekleniyor", "swing_reason": "Gövde bekleniyor",
                        "motion_reason": "Gövde bekleniyor"}
        if not visible:
            if self.last_time is not None and now-self.last_time > .3:
                self.clear_transients()
            fallback = [SimpleNamespace(x=.5,y=.5,z=0,visibility=0) for _ in range(33)]
            fallback[11].x, fallback[12].x = .35,.65
            fallback[11].y = fallback[12].y = .6
            ids = handedness or ["left" if h[0].x < .5 else "right" for h in hands]
            self.hand_controls.update(hands,shapes,ids,fallback,self.scale,now,self.tolerance,result)
            self.last_y = None
            self.last_time = now
            self.baseline = None
            result.calibrated = True
            result.mode = "Nişan" if result.aim else "Swing" if result.swing else "El kontrolü · zıplama için gövde gerekli"
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
            self.baseline, self.center, self.scale = y,center,scale
            self.last_y = y
            self.shoulder_baseline = shoulder_y
        if self.shoulder_baseline is None:
            self.shoulder_baseline = shoulder_y
        result.calibrated = True
        # Calibrated width keeps a torso turn from amplifying motion thresholds.
        observed_scale = scale
        scale = self.scale
        velocity = (self.last_y - y) / scale / dt if self.last_y is not None and 0 < dt <= 0.30 else 0.0
        displacement = (self.baseline - y) / scale
        if abs(displacement) < .06:
            if self.dodge_neutral_since is None:
                self.dodge_neutral_since = now
            if now-self.dodge_neutral_since >= .3:
                self.dodge_ready = True
        else:
            self.dodge_neutral_since = None
        shoulder_dip = (shoulder_y-self.shoulder_baseline)/scale
        if displacement < -0.20 and shoulder_dip > .16 and self.dodge_ready and self.crouch_since is None:
            self.crouch_since = now
        if self.crouch_since is not None:
            self.jump_block_until = now + 0.4
            if displacement > -0.06:
                if 0.20 <= now - self.crouch_since <= 2.5 and velocity > 0.15 and now - self.last_dodge > 0.65:
                    result.dodge = True
                    self.last_dodge = now
                    self.dodge_ready = False
                self.crouch_since = None
        if abs(displacement) < threshold * 0.4:
            self.jump_ready = True
        if (self.jump_ready and now > self.jump_block_until and displacement > threshold
                and velocity > 0.65 and now - self.last_jump > 0.65):
            result.jump = True
            self.jump_ready = False
            self.last_jump = now

        identities = [handedness[i] if handedness and i < len(handedness)
                      else ("left" if h[0].x < center else "right") for i,h in enumerate(hands)]
        suppress_attack = self.hand_controls.update(hands, shapes, identities, pose, scale, now, self.tolerance, result)
        if result.swing or result.swing_exit:
            self.body_block_until = now+.4
        if now < self.body_block_until:
            result.jump = result.dodge = False
            self.crouch_since = None
            self.baseline,self.shoulder_baseline = y,shoulder_y
        if suppress_attack:
            self.attack_block_until = now + .25

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
                if (shape == previous[3] == "fist" and ready and not result.aim and not result.swing
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

        result.mode = "Swing çıkışı" if result.swing_exit else "Nişan" if result.aim else "Swing" if result.swing else "Eğilme" if self.crouch_since is not None else "Serbest"
        if not result.jump and self.crouch_since is None and abs(velocity) < .2:
            alpha = min(1,max(dt,0)/.6)
            self.baseline += (y-self.baseline)*alpha
            self.shoulder_baseline += (shoulder_y-self.shoulder_baseline)*alpha
        self.last_y, self.last_time = y, now
        return result
