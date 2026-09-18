"""Hand-owned mouse sessions and mutually exclusive action gestures."""

from math import dist


def palm_center(hand):
    return tuple(sum(getattr(hand[i], axis) for i in (0, 5, 9, 13, 17)) / 5 for axis in ("x", "y"))


class HandControls:
    def __init__(self):
        self.mode = None
        self.owner = None
        self.anchor = None
        self.gates = {}
        self.fired = set()
        self.release_since = {}
        self.previous_palms = None
        self.clap_armed = False
        self.last_clap = -10
        self.salute_since = None
        self.lowered_since = None
        self.previous_hands = {}

    def edge(self, key, active, now, dwell=.16):
        if not active:
            self.gates.pop(key, None)
            self.release_since.setdefault(key, now)
            if now - self.release_since[key] >= .15:
                self.fired.discard(key)
            return False
        released = self.release_since.pop(key, None)
        if released is not None and now-released >= .15:
            self.fired.discard(key)
        self.gates.setdefault(key, now)
        if key not in self.fired and now - self.gates[key] >= dwell:
            self.fired.add(key)
            return True
        return False

    def update(self, hands, shapes, identities, pose, scale, now, slop, result):
        shoulder = (pose[11].y + pose[12].y) / 2
        center = (pose[11].x + pose[12].x) / 2
        nose = pose[0]
        palms = [palm_center(h) for h in hands]
        # A single rotating palm can change the model's handedness label.
        if len(hands) == 1 and self.owner in self.previous_hands:
            previous = self.previous_hands[self.owner]
            if now-previous[0] <= .3 and dist(previous[1],palms[0]) < scale*.8:
                identities = [self.owner]
        mapping = dict(zip(identities, range(len(hands))))
        if self.owner not in mapping:
            self.mode = self.owner = self.anchor = None
        upward = [h[0].y < shoulder - scale*.15 and h[8].y < h[0].y-scale*.12
                  and h[20].y < h[0].y-scale*.12 for h in hands]
        face_sign = [shape == "two" and abs((h[8].x+h[12].x)/2-nose.x) < scale*.9
                     and nose.y-scale*.8 < (h[8].y+h[12].y)/2 < shoulder+scale*.1
                     and (h[8].y+h[12].y)/2 < h[0].y-scale*.08 for h,shape in zip(hands,shapes)]
        paired_sign = len(hands) == 2 and all(s in ("two", "point") for s in shapes)
        prayer = paired_sign
        if prayer:
            tips = [(h[8].x,h[8].y) for h in hands]
            prayer = (dist(tips[0], tips[1]) < scale*.30 and dist(palms[0],palms[1]) < scale*.9
                      and all(h[8].y < h[0].y-scale*.15 for h in hands))
        result.web_attack = self.edge("prayer", prayer, now, .25)
        clap_near = False
        clap_approach = False
        if len(hands) == 2 and shapes == ["open", "open"]:
            gap = dist(palms[0],palms[1])/scale
            if gap > 1.0:
                self.clap_armed = True
            previous = self.previous_palms
            closing = (previous[1]-gap)/(now-previous[0]) if previous and 0 < now-previous[0] <= .3 else 0
            clap_near = gap < .75
            clap_approach = self.clap_armed and closing > .8
            if self.clap_armed and gap < .55 and closing > .8 and now-self.last_clap > .35:
                result.gadget = True
                self.last_clap = now
                self.clap_armed = False
            self.previous_palms = (now,gap)
        else:
            self.previous_palms = None
            self.clap_armed = False

        # A fist at the anatomical left chest, held briefly, is the salute.
        heart = (center + (pose[11].x-center)*.5, shoulder+scale*.4)
        salute = False
        for identity,shape,palm in zip(identities,shapes,palms):
            previous = self.previous_hands.get(identity)
            speed = dist(previous[1],palm)/scale/(now-previous[0]) if previous and now>previous[0] else 0
            if shape == "fist" and abs(palm[0]-heart[0]) < scale*.4 and abs(palm[1]-heart[1]) < scale*.45 and speed < .6:
                salute = True
        self.previous_hands = {identity:(now,palm) for identity,palm in zip(identities,palms)}
        busy = paired_sign or clap_near or clap_approach or result.gadget
        if salute and not busy and self.mode != "aim":
            if self.salute_since is None:
                self.salute_since = now
            result.forward = now-self.salute_since >= .25
        else:
            self.salute_since = None

        aim_events = []
        swing_events = []
        for i, identity in enumerate(identities):
            if self.edge((identity,"aim"), face_sign[i] and not busy and not paired_sign, now):
                aim_events.append(i)
            key = (identity,"swing")
            if shapes[i] == "web" and upward[i] and not busy:
                self.gates.setdefault(key,now)
                if now-self.gates[key] >= .08:
                    swing_events.append(i)
            else:
                self.gates.pop(key,None)
        # Missing identities cannot inherit a prior gesture's debounce state.
        for key in list(self.gates):
            if isinstance(key,tuple) and key[0] not in mapping:
                self.gates.pop(key,None)
                self.fired.discard(key)
        if aim_events:
            i = aim_events[0]
            if self.mode == "aim" and self.owner == identities[i]:
                self.mode = self.owner = None
            else:
                self.mode, self.owner = "aim", identities[i]
            self.anchor = None
        elif swing_events:
            i = next((i for i in swing_events if identities[i] == self.owner), swing_events[0])
            if self.mode != "swing" or self.owner != identities[i]:
                self.mode, self.owner = "swing", identities[i]
                self.anchor = None
                result.swing_cast = True
        elif self.mode == "swing":
            self.mode = self.owner = self.anchor = None

        if self.mode == "aim" and self.owner in mapping:
            hand = hands[mapping[self.owner]]
            if hand[0].y > shoulder + scale*1.3:
                if self.lowered_since is None:
                    self.lowered_since = now
                if now-self.lowered_since > .25:
                    self.mode = self.owner = self.anchor = None
            else:
                self.lowered_since = None
        else:
            self.lowered_since = None

        result.aim = self.mode == "aim"
        result.swing = self.mode == "swing"
        if result.aim:
            result.forward = False
        result.mouse_session = f"{self.mode}:{self.owner}" if self.mode else ""
        if self.mode and self.owner in mapping:
            point = palms[mapping[self.owner]]
            if self.mode == "swing":
                if self.anchor is None:
                    self.anchor = point
                offset = (point[0]-self.anchor[0])/scale
                zone = max(.10, slop*5)
                result.look_x = max(-1,min(1, (abs(offset)-zone)/.65)) * (1 if offset>0 else -1) if abs(offset)>zone else 0
                result.debug["swing_center"] = self.anchor
            elif self.anchor is not None and not busy and not salute:
                delta = [(point[i]-self.anchor[i])/scale for i in range(2)]
                if max(abs(v) for v in delta) < .7:
                    output = [max(0,abs(v)-slop)*(1 if v>0 else -1) for v in delta]
                    result.mouse_dx, result.mouse_dy = output
                    self.anchor = tuple(self.anchor[i]+output[i]*scale for i in range(2))
                else:
                    self.anchor = point
            else:
                self.anchor = point
            result.debug.update(pointer=point, pointer_anchor=self.anchor, pointer_slop=(max(.10,slop*5) if self.mode == "swing" else slop)*scale)
        else:
            self.anchor = None
        result.debug.update(aim_reason="AKTİF · eli fare gibi taşı" if result.aim else "Yüz hizasında işaret + orta parmak",
            swing_reason="AKTİF · el merkezi sağ/sol bakışı yönetiyor" if result.swing else "Yukarı ağ işareti bekleniyor",
            motion_reason=f"El: {self.owner or '—'} · E: {'alkış' if result.gadget else '—'} · F: {'işaret' if prayer else '—'} · W: {'selam' if salute else '—'}",
            heart=heart)
        return busy or salute or bool(aim_events) or result.aim or result.swing
