import tempfile
import unittest
from pathlib import Path

from test_controls import FakeInput
from test_motion import calibrated, body, palm, WEB, OPEN, REGIONS
from test_calibration import face
from webmotion.gestures import Decision
from webmotion.keyboard import InputGuard
from webmotion.regions import valid_regions, save_regions, load_regions


class RegionTests(unittest.TestCase):
    def test_web_in_each_box_requires_dwell_and_releases_on_exit(self):
        for x,y,side,forward in ((.1,.4,-1,False),(.9,.4,1,False),(.5,.1,0,True)):
            with self.subTest(x=x):
                g = calibrated()
                first = g.update([palm(x=x,y=y)],body(),1.4)
                self.assertEqual((first.steer,first.forward),(0,False))
                held = g.update([palm(x=x,y=y)],body(),1.57)
                self.assertEqual((held.steer,held.forward),(side,forward))
                released = g.update([palm(x=.5,y=.4)],body(),1.65)
                self.assertEqual((released.steer,released.forward),(0,False))

    def test_open_hand_in_direction_box_does_not_move(self):
        g = calibrated()
        for t in (1.4,1.6,1.8):
            r = g.update([palm(OPEN,x=.1,y=.4)],body(),t)
            self.assertEqual(r.steer,0)

    def test_two_different_direction_boxes_cancel(self):
        g = calibrated()
        for t in (1.4,1.6,1.8):
            r = g.update([palm(x=.1,y=.4),palm(x=.9,y=.4)],body(),t)
            self.assertEqual((r.steer,r.forward),(0,False))

    def test_manual_aim_uses_both_wrists_not_learned_height(self):
        g = calibrated()
        for t in (1.4,1.6):
            r = g.update([palm(x=.3,y=.75),palm(x=.7,y=.75)],body(),t)
        self.assertTrue(r.aim)
        self.assertEqual((r.steer,r.forward),(0,False))
        g.update([palm(x=.1,y=.75),palm(x=.7,y=.75)],body(),1.7)
        self.assertFalse(g.update([palm(x=.1,y=.75),palm(x=.7,y=.75)],body(),1.85).aim)

    def test_missing_regions_never_create_movement_or_aim(self):
        g = calibrated()
        g.regions = {}
        for t in (1.4,1.6,1.8):
            r = g.update([palm(x=.3,y=.75),palm(x=.7,y=.75)],body(),t)
            self.assertEqual((r.aim,r.steer,r.forward),(False,0,False))

    def test_hand_loss_and_tracking_gap_clear_region_dwell(self):
        g = calibrated()
        g.update([palm(x=.1,y=.4)],body(),1.4)
        self.assertEqual(g.update([palm(x=.1,y=.4)],body(),1.6).steer,-1)
        self.assertEqual(g.update([],body(),1.7).steer,0)
        self.assertEqual(g.update([palm(x=.1,y=.4)],body(),1.8).steer,0)
        self.assertEqual(g.update([palm(x=.1,y=.4)],body(),2.2).steer,0)

    def test_head_camera_works_while_aiming_and_wrists_do_not_move_it(self):
        g = calibrated()
        for t in (1.4,1.6):
            r = g.update([palm(x=.3,y=.75),palm(x=.7,y=.75)],face(),t)
        self.assertTrue(r.aim)
        self.assertEqual((r.look_x,r.look_y),(0,0))
        g.update([palm(x=.4,y=.7),palm(x=.6,y=.7)],face(.4),1.7)
        r = g.update([palm(x=.4,y=.7),palm(x=.6,y=.7)],face(.4),1.9)
        self.assertGreater(r.look_x,0)
        self.assertEqual(g.update([palm(x=.4,y=.7),palm(x=.6,y=.7)],face(),2).look_x,0)

    def test_regions_round_trip_and_invalid_rectangles(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'regions.json'
            save_regions(path,REGIONS)
            self.assertEqual(load_regions(path),REGIONS)
            for rect in ([0,0,0,0], [0,0,2,1], [0,0,float('nan'),1], REGIONS['left']):
                data = dict(REGIONS, right=rect)
                self.assertFalse(valid_regions(data))
                with self.assertRaises(ValueError):
                    save_regions(path,data)
            self.assertEqual(load_regions(path),REGIONS)

    def test_forward_releases_on_focus_loss_aim_staleness_and_f8(self):
        for reason in ('focus','aim','stale','f8'):
            b = FakeInput()
            guard = InputGuard(b)
            guard.configure(True)
            guard.publish_decision(Decision(forward=True),1)
            guard.step(1)
            self.assertIn((guard.W,True),b.events)
            if reason == 'focus': b.process = 'notepad.exe'
            if reason == 'aim': guard.publish_decision(Decision(aim=True,forward=True),1.1)
            if reason == 'f8': b.panic = True
            guard.step(1.4 if reason == 'stale' else 1.1)
            self.assertNotIn(guard.W,guard.held)

    def test_head_camera_uses_elapsed_time_and_stops_in_background(self):
        totals = []
        for hz in (50,100):
            b = FakeInput()
            guard = InputGuard(b)
            guard.configure(True)
            for i in range(hz+1):
                t = 1+i/hz
                guard.publish_decision(Decision(look_x=.5),t)
                guard.step(t)
            totals.append(sum(x for x,y in b.moves))
            b.process = 'notepad.exe'
            count = len(b.moves)
            guard.step(2.01)
            self.assertEqual(len(b.moves),count)
        self.assertAlmostEqual(totals[0],225,delta=1)
        self.assertAlmostEqual(totals[0],totals[1],delta=1)

    def test_camera_inversion_does_not_swap_region_key_labels(self):
        b = FakeInput()
        guard = InputGuard(b)
        guard.configure(True)
        guard.invert_x = True
        guard.publish_decision(Decision(steer=-1),1)
        guard.step(1)
        self.assertIn((guard.A,True),b.events)
        self.assertNotIn((guard.D,True),b.events)
