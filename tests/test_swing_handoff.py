import random
import unittest

from test_motion import calibrated, palm, body, WEB, OPEN, FIST
from test_controls import FakeInput
from webmotion.gestures import Decision
from webmotion.keyboard import InputGuard
from webmotion.hand_identity import HandIdentity


class SwingHandoffTests(unittest.TestCase):
    def start(self):
        g = calibrated()
        for t in (1.4, 1.5):
            r = g.update([palm(WEB,x=.3,y=.3)],body(),t,handedness=['L'])
        self.assertTrue(r.swing)
        return g

    def test_lower_spare_hand_never_takes_control(self):
        g = self.start()
        rng = random.Random(14)
        for i in range(160):
            owner = palm(WEB if i%7 else OPEN,x=.32,y=.3)
            spare = palm(rng.choice([OPEN, FIST, WEB]),x=.75,y=.75)
            hands,ids = ([owner,spare],['L','R']) if i%2 else ([spare,owner],['R','L'])
            r = g.update(hands,body(),1.55+i*.04,handedness=ids)
            self.assertTrue(r.swing)
            self.assertEqual(r.mouse_session,'swing:L')
            self.assertFalse(r.swing_cast or r.swing_exit or r.steer or r.forward or r.backward)

    def test_alternating_hands_recenter_without_recast(self):
        g = self.start()
        for j in range(6):
            t = 1.6+j*.3
            new,old = ('R','L') if j%2==0 else ('L','R')
            x = .7 if new=='R' else .3
            hands = [palm(WEB,x=x,y=.3),palm(OPEN,x=1-x,y=.75)]
            for stamp in (t,t+.1):
                r = g.update(hands,body(),stamp,handedness=[new,old])
                self.assertTrue(r.swing)
                self.assertFalse(r.swing_cast or r.steer or r.forward or r.backward)
            self.assertTrue(r.swing_handoff)
            self.assertEqual(r.mouse_session,'swing:'+new)
            self.assertEqual(r.look_x,0)

    def test_both_webs_do_not_steal_valid_owner(self):
        g = self.start()
        for t in (1.6,1.7,1.8):
            r = g.update([palm(WEB,x=.7,y=.2),palm(WEB,x=.35,y=.3)],body(),t,handedness=['R','L'])
            self.assertEqual(r.mouse_session,'swing:L')
            self.assertFalse(r.swing_handoff)

    def test_lost_owner_open_spare_stays_locked_indefinitely(self):
        g = self.start()
        for i in range(100):
            r = g.update([palm(OPEN,x=.7+(i%2)*.15,y=.7)],body(),1.6+i*.05,handedness=['R'])
            self.assertTrue(r.walk_locked)
            self.assertFalse(r.steer or r.forward or r.backward)
        self.assertFalse(r.swing)
        g.hand_controls.resume_walking()
        r = g.update([palm(OPEN,x=.7,y=.7)],body(),6.6,handedness=['R'])
        self.assertFalse(r.walk_locked or r.steer)
        r = g.update([palm(OPEN,x=.8,y=.6)],body(),6.7,handedness=['R'])
        self.assertTrue(r.forward)
        self.assertEqual(r.steer,1)

    def test_short_loss_retains_original_anchor(self):
        g = self.start()
        r = g.update([],body(),1.6)
        self.assertTrue(r.swing)
        self.assertEqual(r.look_x,0)
        r = g.update([palm(WEB,x=.4,y=.3)],body(),1.7,handedness=['L'])
        self.assertTrue(r.swing)
        self.assertGreater(r.look_x,0)
        self.assertFalse(r.swing_cast)

    def test_opposite_label_far_hand_is_not_old_owner(self):
        tracker = HandIdentity()
        self.assertEqual(tracker.update([(.3,.3)],['L'],1,.3)[0],['L'])
        self.assertEqual(tracker.update([(.6,.3)],['R'],1.1,.3)[0],['R'])

    def test_label_flip_nearby_keeps_identity(self):
        tracker = HandIdentity()
        tracker.update([(.3,.3)],['L'],1,.3)
        self.assertEqual(tracker.update([(.32,.3)],['R'],1.1,.3)[0],['L'])

    def test_overlapping_hands_freeze_camera(self):
        g = self.start()
        r = g.update([palm(WEB,x=.4,y=.3),palm(WEB,x=.41,y=.3)],body(),1.6,handedness=['L','R'])
        self.assertTrue(r.swing)
        self.assertEqual(r.look_x,0)
        self.assertFalse(r.swing_handoff)

    def test_reset_and_long_frame_gap_keep_walk_lock(self):
        g = self.start()
        r = g.update([palm(OPEN,x=.7,y=.7)],body(),2.5,handedness=['R'])
        self.assertTrue(r.walk_locked)
        self.assertFalse(r.steer or r.forward)

    def test_dispatch_sticky_lock_survives_focus_disable_and_stale(self):
        b = FakeInput(); guard = InputGuard(b); guard.configure(True)
        guard.publish_decision(Decision(swing=True),1);guard.step(1)
        self.assertFalse(guard.resume_movement())
        for i in range(100):
            t = 1.2+i*.05
            guard.publish_decision(Decision(steer=(-1)**i,forward=i%2==0,backward=i%2==1),t)
            guard.step(t)
            self.assertFalse(guard.held & {guard.W,guard.A,guard.S,guard.D})
        guard.step(9)
        b.process='notepad.exe';guard.step(9.1)
        guard.configure(False);guard.configure(True)
        self.assertTrue(guard.movement_locked)
        b.process='spider-man.exe'
        guard.publish_decision(Decision(forward=True),10);guard.step(10)
        self.assertNotIn(guard.W,guard.held)
        self.assertTrue(guard.resume_movement())
        guard.publish_decision(Decision(forward=True),10.1);guard.step(10.1)
        self.assertIn(guard.W,guard.held)

    def test_handoff_has_no_shift_release_or_second_space(self):
        b=FakeInput();guard=InputGuard(b);guard.configure(True)
        guard.publish_decision(Decision(swing=True,swing_cast=True),1)
        for t in (1,1.11,1.17):guard.step(t)
        b.events.clear()
        for i in range(10):
            t=1.2+i*.05
            guard.publish_decision(Decision(swing=True,swing_handoff=True),t);guard.step(t)
        self.assertEqual(b.events,[])
        self.assertEqual(guard.held,{guard.SHIFT})
