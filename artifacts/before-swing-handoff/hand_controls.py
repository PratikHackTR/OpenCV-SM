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
        self.trigger_ready = False
        self.move_owner = None
        self.move_anchor = None
        self.last_web = -10
        self.swing_bad_since = None
        self.fist_since = None
        self.action_block_until = 0

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
        if self.mode == "swing":
            index = mapping[self.owner]
            shape = shapes[index]
            if shape == "web" and upward[index]:
                self.last_web = now
                self.swing_bad_since = self.fist_since = None
            elif shape == "fist" and now-self.last_web <= 2:
                self.fist_since = now if self.fist_since is None else self.fist_since
                if now-self.fist_since >= .05:
                    result.swing_exit = True
                    self.action_block_until = now+.35
                    self.mode = self.owner = self.anchor = None
            else:
                self.fist_since = None
                if self.swing_bad_since is None:
                    self.swing_bad_since = now
                # A short misclassification must not restart Space -> Shift.
                if shape == "open" or hands[index][0].y > shoulder+scale*.15 or now-self.swing_bad_since > .20:
                    self.mode = self.owner = self.anchor = None
            if self.mode == "swing":
                result.swing = True
                result.mouse_session = f"swing:{self.owner}"
                self.camera(palms[index],scale,slop,result)
            self.move_owner = self.move_anchor = None
            self.previous_hands = {identity:(now,palm) for identity,palm in zip(identities,palms)}
            result.debug.update(aim_reason="Swing kilidi",swing_reason="Yumruk → Space, ardından Shift bırak" if result.swing_exit else "Swing · yalnızca kamera",
                                motion_reason="Swing sırasında WASD / Ctrl / E / F / saldırı kapalı")
            return True
        if now < self.action_block_until:
            result.debug.update(motion_reason="Swing çıkışı tamamlanıyor",aim_reason="Kısa geçiş",swing_reason="Yeni ağ işareti bekleniyor")
            return True
        face_sign = [shape == "two" for shape in shapes]
        busy = False
        gap = None
        closing = 0
        if len(palms) == 2:
            gap = dist(palms[0],palms[1])/scale
            if gap > 1.0:
                self.clap_armed = True
            previous = self.previous_palms
            closing = (previous[1]-gap)/(now-previous[0]) if previous and 0 < now-previous[0] <= .3 else 0
            converging = []
            for i,identity in enumerate(identities):
                old = self.previous_hands.get(identity)
                if old:
                    motion = [palms[i][j]-old[1][j] for j in range(2)]
                    toward = [palms[1-i][j]-palms[i][j] for j in range(2)]
                    converging.append(sum(a*b for a,b in zip(motion,toward)) > scale*scale*.005)
            bilateral = len(converging)==2 and all(converging)
            busy = self.clap_armed and closing > .7 and bilateral and gap < 1.2
            if self.clap_armed and gap < .8 and closing > .7 and bilateral and now-self.last_clap > .4:
                result.web_attack = True
                self.last_clap = now
                self.clap_armed = False
            self.previous_palms = (now,gap)
        else:
            # Joined hands may become one detection. Require a preceding approach
            # and converging, visible body wrists before completing the clap.
            previous = self.previous_palms
            wrists_visible = all(pose[i].visibility >= .6 for i in (15,16))
            if (self.clap_armed and previous and now-previous[0] < .18
                    and previous[1] < 1.2 and getattr(self,"last_closing",0) > .7
                    and wrists_visible and dist((pose[15].x,pose[15].y),(pose[16].x,pose[16].y))/scale < .6
                    and now-self.last_clap > .4):
                result.web_attack = True
                self.last_clap = now
                self.clap_armed = False
            self.previous_palms = None
        self.last_closing = closing if busy else 0
        busy = busy or result.web_attack
        self.previous_hands = {identity:(now,palm) for identity,palm in zip(identities,palms)}
        aim_events = []
        swing_events = []
        for i, identity in enumerate(identities):
            if self.edge((identity,"aim"), face_sign[i] and not busy, now):
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
        if aim_events and self.mode != "aim":
            i = aim_events[0]
            self.mode, self.owner = "aim", identities[i]
            self.anchor = None
            self.trigger_ready = True
        elif swing_events and self.mode != "aim":
            i = next((i for i in swing_events if identities[i] == self.owner), swing_events[0])
            if self.mode != "swing" or self.owner != identities[i]:
                self.mode, self.owner = "swing", identities[i]
                self.anchor = None
                result.swing_cast = True
                self.last_web = now
                self.swing_bad_since = self.fist_since = None
        elif self.mode == "swing":
            self.mode = self.owner = self.anchor = None
        if self.mode == "aim" and self.owner in mapping:
            shape = shapes[mapping[self.owner]]
            if shape == "two":
                self.trigger_ready = True
            elif shape == "fist" and self.trigger_ready and not busy:
                result.gadget = True
                self.trigger_ready = False
            # Open palm is an explicit aim exit, independent of frame position.
            if self.edge("exit_aim",shape == "open",now,.25):
                self.mode = self.owner = self.anchor = None
        else:
            self.edge("exit_aim",False,now)
        if self.mode is None:
            for i,shape in enumerate(shapes):
                if self.edge((identities[i],"look"),shape == "point" and not busy,now,.10):
                    self.mode,self.owner = "look",identities[i]
                    self.anchor = None
                    break
        elif self.mode == "look" and shapes[mapping[self.owner]] != "point":
            self.gates.pop((self.owner,"look"),None)
            self.fired.discard((self.owner,"look"))
            self.mode = self.owner = self.anchor = None
        result.aim = self.mode == "aim"
        result.swing = self.mode == "swing"
        result.mouse_session = f"{self.mode}:{self.owner}" if self.mode else ""
        if self.mode and self.owner in mapping:
            self.camera(palms[mapping[self.owner]],scale,slop,result,paused=busy)
        else:
            self.anchor = None
        movers = [i for i,shape in enumerate(shapes) if shape == "open" and identities[i] != self.owner]
        if movers and not result.swing and not busy and not (len(hands)==2 and shapes==["open","open"]):
            i = next((i for i in movers if identities[i]==self.move_owner),movers[0])
            if self.move_owner != identities[i] or self.move_anchor is None:
                self.move_owner,self.move_anchor = identities[i],palms[i]
            dx,dy = [(palms[i][j]-self.move_anchor[j])/scale for j in range(2)]
            result.steer = -1 if dx < -.22 else 1 if dx > .22 else 0
            result.forward,result.backward = dy < -.22,dy > .22
            result.debug.update(move_anchor=self.move_anchor,move_hand=palms[i],move_threshold=.22*scale,move_owner=self.move_owner,move_offset=(dx,dy))
        else:
            self.move_owner = self.move_anchor = None
        result.debug.update(aim_reason="AKTİF · kapat: E × 2 / avucu aç: çık" if result.aim else "İki parmak: kadrajın her yerinde nişan",
            swing_reason="AKTİF · başlangıç merkezi sağ/sol bakış" if result.swing else "Yukarı ağ işareti",
            motion_reason=f"Fare: {self.owner or '—'} · Yürüme: {self.move_owner or '—'} · F mesafe: {gap if gap is not None else -1:.2f} · yaklaşma: {closing:.1f}")
        if result.swing:
            result.aim = result.gadget = result.web_attack = result.forward = result.backward = False
            result.steer = 0
        return busy or bool(movers) or self.mode is not None

    def camera(self, point, scale, slop, result, paused=False):
        if self.anchor is None:
            self.anchor = point
        zone = max(.08,slop*4)
        def speed(value):
            value /= scale
            magnitude = max(0,abs(value)-zone)
            return min(2.5,magnitude*1.5+magnitude*magnitude*1.5)*(1 if value>0 else -1)
        if not paused:
            result.look_x = speed(point[0]-self.anchor[0])
            result.look_y = speed(point[1]-self.anchor[1]) if self.mode != "swing" else 0
        result.debug.update(pointer=point,pointer_anchor=self.anchor,pointer_slop=zone*scale)
