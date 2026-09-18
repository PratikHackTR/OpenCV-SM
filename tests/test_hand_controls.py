import unittest

from test_motion import calibrated, palm, body, WEB, OPEN, FIST
from test_calibration import face
from test_controls import FakeInput
from webmotion.gestures import Decision
from webmotion.keyboard import InputGuard

TWO = [True, True, False, False]
POINT = [True, False, False, False]


class HandControlsTests(unittest.TestCase):
    def aim(self):
        g = calibrated()
        h = palm(TWO,x=.5,y=.6)
        g.update([h],body(),1.4,handedness=['L'])
        self.assertTrue(g.update([h],body(),1.6,handedness=['L']).aim)
        return g

    def test_swing_center_hold_release_and_fresh_center(self):
        g = calibrated()
        g.update([palm(x=.3,y=.3)],body(),1.4,handedness=['L'])
        r = g.update([palm(x=.3,y=.3)],body(),1.5,handedness=['L'])
        self.assertTrue(r.swing)
        self.assertEqual(r.look_x,0)
        self.assertGreater(g.update([palm(x=.42,y=.3)],body(),1.6,handedness=['L']).look_x,0)
        self.assertEqual(g.update([palm(x=.3,y=.3)],body(),1.7,handedness=['L']).look_x,0)
        r = g.update([palm(OPEN,x=.42,y=.3)],body(),1.8,handedness=['L'])
        self.assertTrue(r.swing)  # Brief classification loss retains Shift.
        self.assertEqual(r.look_x,0)
        g.update([palm(OPEN,x=.42,y=.3)],body(),2.0,handedness=['L'])
        self.assertFalse(g.update([palm(OPEN,x=.42,y=.3)],body(),2.2,handedness=['L']).swing)
        g.update([],body(),2.4)
        g.update([palm(x=.42,y=.3)],body(),2.6,handedness=['L'])
        r = g.update([palm(x=.42,y=.3)],body(),2.7,handedness=['L'])
        self.assertTrue(r.swing)
        self.assertEqual(r.look_x,0)


    def test_swing_owner_survives_output_order_change(self):
        g = calibrated()
        hands = [palm(x=.3,y=.3),palm(OPEN,x=.8,y=.7)]
        g.update(hands,body(),1.4,handedness=['L','R'])
        g.update(hands,body(),1.5,handedness=['L','R'])
        r = g.update([hands[1],palm(x=.4,y=.3)],body(),1.6,handedness=['R','L'])
        self.assertGreater(r.look_x,0)
        self.assertEqual(r.mouse_session,'swing:L')


    def test_finger_shapes_and_jitter_do_not_move_pointer(self):
        g = self.aim()
        for i in range(10):
            r = g.update([palm(OPEN if i%2 else FIST,x=.5+(-1)**i*.002,y=.6)],body(),1.7+i*.05,handedness=['L'])
            self.assertEqual((r.mouse_dx,r.mouse_dy),(0,0))


    def test_aim_open_palm_loss_and_gap_release(self):
        for mode in ('lower','loss','gap'):
            g = self.aim()
            if mode == 'lower':
                g.update([palm(OPEN,x=.5,y=1.1)],body(),1.7,handedness=['L'])
                r = g.update([palm(OPEN,x=.5,y=1.1)],body(),1.98,handedness=['L'])
            elif mode == 'loss':
                r = g.update([],body(),1.7)
            else:
                r = g.update([palm(OPEN,x=.5,y=.6)],body(),2,handedness=['L'])
            self.assertFalse(r.aim)

    def test_aim_rejects_detector_jump_and_single_hand_label_flip(self):
        g = self.aim()
        r = g.update([palm(OPEN,x=.51,y=.6)],body(),1.7,handedness=['R'])
        self.assertTrue(r.aim)
        self.assertEqual(r.mouse_session,'aim:L')
        r = g.update([palm(OPEN,x=.95,y=.6)],body(),1.8,handedness=['L'])
        self.assertEqual((r.mouse_dx,r.mouse_dy),(0,0))






class HandDispatchTests(unittest.TestCase):

    def test_e_and_f_work_outside_aim_and_release(self):
        b = FakeInput()
        g = InputGuard(b)
        g.configure(True)
        g.publish_decision(Decision(gadget=True,web_attack=True),1)
        g.step(1)
        self.assertIn((g.E,True),b.events)
        self.assertIn((g.F,True),b.events)
        g.step(1.11)
        self.assertFalse(g.held)

    def test_f8_releases_w_f_and_right_mouse(self):
        for decision in (Decision(forward=True,web_attack=True),Decision(aim=True,mouse_session='aim:L')):
            b = FakeInput()
            g = InputGuard(b)
            g.configure(True)
            g.publish_decision(decision,1)
            g.step(1)
            self.assertTrue(g.held or g.mouse_held)
            b.panic = True
            g.step(1.01)
            self.assertFalse(g.held or g.mouse_held)

    def test_swing_mouse_uses_time_not_frame_count_and_neutral_stops(self):
        totals=[]
        for hz in (50,100):
            b = FakeInput()
            g = InputGuard(b)
            g.configure(True)
            for i in range(hz+1):
                t=1+i/hz
                g.publish_decision(Decision(swing=True,look_x=.5,mouse_session='swing:L'),t)
                g.step(t)
            totals.append(sum(x for x,y in b.moves))
            count=len(b.moves)
            g.publish_decision(Decision(swing=True,mouse_session='swing:L'),2.1)
            g.step(2.1)
            self.assertEqual(len(b.moves),count)
        self.assertAlmostEqual(totals[0],225,delta=1)
        self.assertAlmostEqual(totals[0],totals[1],delta=1)
