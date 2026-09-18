import unittest
from test_motion import calibrated,palm,body,OPEN,FIST,WEB
from test_controls import FakeInput
from test_hand_controls import TWO
from webmotion.gestures import Decision
from webmotion.keyboard import InputGuard


class SwingIsolationTests(unittest.TestCase):
    def swing(self):
        g=calibrated()
        g.update([palm(WEB,x=.3,y=.3)],body(),1.4,handedness=['L'])
        self.assertTrue(g.update([palm(WEB,x=.3,y=.3)],body(),1.5,handedness=['L']).swing)
        return g

    def test_body_motion_and_other_hand_cannot_interrupt_swing(self):
        g=self.swing()
        for i in range(40):
            hands=[palm(WEB,x=.4,y=.3),palm(TWO if i%2 else OPEN,x=.7,y=.6)]
            r=g.update(hands,body(.4 if i%2 else .52),1.6+i*.05,handedness=['L','R'])
            self.assertTrue(r.swing)
            self.assertFalse(r.jump or r.dodge or r.aim or r.gadget or r.web_attack or r.attack or r.forward or r.backward or r.steer)
            self.assertGreater(r.look_x,0)

    def test_brief_shape_uncertainty_does_not_restart_swing(self):
        g=self.swing()
        r=g.update([palm(TWO,x=.3,y=.3)],body(),1.6,handedness=['L'])
        self.assertTrue(r.swing)
        r=g.update([palm(WEB,x=.3,y=.3)],body(),1.7,handedness=['L'])
        self.assertTrue(r.swing)
        self.assertFalse(r.swing_cast)

    def test_owner_fist_exits_once_and_new_web_restarts(self):
        g=self.swing()
        g.update([palm(FIST,x=.3,y=.3)],body(),1.6,handedness=['L'])
        r=g.update([palm(FIST,x=.3,y=.3)],body(),1.72,handedness=['L'])
        self.assertTrue(r.swing_exit)
        self.assertFalse(r.swing or r.attack or r.dodge or r.gadget)
        for t in (1.8,1.95,2.1):
            self.assertFalse(g.update([palm(FIST,x=.3,y=.3)],body(),t,handedness=['L']).swing_exit)
        g.update([palm(WEB,x=.3,y=.3)],body(),2.2,handedness=['L'])
        self.assertTrue(g.update([palm(WEB,x=.3,y=.3)],body(),2.3,handedness=['L']).swing_cast)

    def test_other_fist_does_not_exit(self):
        g=self.swing()
        for t in (1.6,1.7):
            r=g.update([palm(WEB,x=.3,y=.3),palm(FIST,x=.7,y=.3)],body(),t,handedness=['L','R'])
            self.assertTrue(r.swing)
            self.assertFalse(r.swing_exit)

    def test_head_only_bobbing_does_not_dodge(self):
        g=calibrated()
        for i in range(80):
            p=body()
            p[0].y += .18 if i%8<4 else 0
            self.assertFalse(g.update([],p,1.4+i*.05).dodge)

    def test_dispatch_blocks_and_clears_every_non_swing_action(self):
        b=FakeInput();g=InputGuard(b);g.configure(True)
        g.publish_decision(Decision(gadget=True,dodge=True,web_attack=True,forward=True,attack=True),1)
        g.step(1)
        g.publish_decision(Decision(swing=True,aim=True,gadget=True,web_attack=True,dodge=True,jump=True,attack=True,forward=True,backward=True,steer=1),1.02)
        g.step(1.02)
        self.assertFalse(g.held-{g.SPACE,g.SHIFT})
        self.assertFalse(g.mouse_held or g.e_edges or g.pulses)

    def test_exit_holds_shift_during_space_then_releases_and_no_repeat(self):
        b=FakeInput();g=InputGuard(b);g.configure(True)
        g.publish_decision(Decision(swing=True),1)
        for t in (1,1.11,1.17):g.step(t)
        self.assertIn(g.SHIFT,g.held)
        b.events.clear()
        g.publish_decision(Decision(swing_exit=True),1.2);g.step(1.2)
        self.assertEqual(g.held,{g.SHIFT,g.SPACE})
        g.publish_decision(Decision(gadget=True,dodge=True,forward=True),1.25);g.step(1.25)
        self.assertEqual(g.held,{g.SHIFT,g.SPACE})
        g.publish_decision(Decision(),1.31);g.step(1.31)
        self.assertEqual(b.events,[(g.SPACE,True),(g.SPACE,False),(g.SHIFT,False)])
        self.assertFalse(g.held)
        g.step(1.4)
        self.assertFalse(g.held)

    def test_focus_loss_cancels_exit_pulse(self):
        b=FakeInput();g=InputGuard(b);g.configure(True)
        g.publish_decision(Decision(swing=True),1);g.step(1);g.step(1.17)
        g.publish_decision(Decision(swing_exit=True),1.2);g.step(1.2)
        b.process='notepad.exe';g.step(1.23)
        self.assertFalse(g.held)
        self.assertIsNone(g.swing_exit_until)

    def test_wasd_overlay_metadata_matches_thresholds(self):
        g=calibrated()
        g.update([palm(OPEN,x=.3,y=.7)],body(),1.4)
        r=g.update([palm(OPEN,x=.4,y=.6)],body(),1.5)
        self.assertAlmostEqual(r.debug['move_threshold'],.22*g.scale)
        self.assertTrue(r.forward)
        self.assertEqual(r.steer,1)
