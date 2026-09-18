import unittest
from test_motion import calibrated,palm,body,OPEN,FIST,WEB
from test_hand_controls import TWO,POINT
from test_controls import FakeInput
from webmotion.gestures import Gestures,Decision
from webmotion.keyboard import InputGuard


class FreeControlsTests(unittest.TestCase):
    def aim(self, x=.5,y=.6,pose=None):
        g=Gestures()
        h=palm(TWO,x=x,y=y)
        p=body() if pose is None else pose
        g.update([h],p,1,handedness=['L'])
        self.assertTrue(g.update([h],p,1.2,handedness=['L']).aim)
        return g,p

    def test_aim_anywhere_without_pose_or_calibration(self):
        for x,y in ((.1,.2),(.9,.9),(.5,.6)):
            g,p=self.aim(x,y,[])
            r=g.update([palm(TWO,x=x,y=y)],p,1.3,handedness=['L'])
            self.assertTrue(r.aim)
            self.assertEqual((r.look_x,r.look_y),(0,0))

    def test_aim_neutral_and_accelerated_unlimited_turn(self):
        g,p=self.aim()
        r=g.update([palm(TWO,x=.6,y=.7)],p,1.3,handedness=['L'])
        self.assertGreater(r.look_x,0)
        self.assertGreater(r.look_y,0)
        speed=r.look_x
        r=g.update([palm(TWO,x=.85,y=.7)],p,1.4,handedness=['L'])
        self.assertGreater(r.look_x,speed)
        r=g.update([palm(TWO,x=.5,y=.6)],p,1.5,handedness=['L'])
        self.assertEqual((r.look_x,r.look_y),(0,0))

    def test_trigger_close_once_until_reopened_and_no_height_exit(self):
        g,p=self.aim(y=.9)
        self.assertTrue(g.update([palm(FIST,x=.5,y=.9)],p,1.3,handedness=['L']).gadget)
        r=g.update([palm(FIST,x=.5,y=.9)],p,1.4,handedness=['L'])
        self.assertTrue(r.aim)
        self.assertFalse(r.gadget)
        g.update([palm(TWO,x=.5,y=.9)],p,1.5,handedness=['L'])
        self.assertTrue(g.update([palm(FIST,x=.5,y=.9)],p,1.6,handedness=['L']).gadget)

    def test_hulk_clap_accepts_curled_hands_and_rearms(self):
        g=calibrated()
        def frame(xs,t):return g.update([palm(FIST,x=x,y=.7) for x in xs],body(),t,handedness=['L','R'])
        frame((.2,.8),1.4)
        r=frame((.4,.6),1.5)
        self.assertTrue(r.web_attack)
        self.assertFalse(r.gadget)
        self.assertFalse(frame((.4,.6),1.6).web_attack)
        frame((.2,.8),1.8)
        self.assertTrue(frame((.4,.6),2).web_attack)

    def test_clap_requires_two_converging_hands_not_single_mouse_hand(self):
        g,p=self.aim()
        g.update([palm(TWO,x=.5,y=.6),palm(OPEN,x=.8,y=.6)],p,1.3,handedness=['L','R'])
        r=g.update([palm(TWO,x=.6,y=.6),palm(OPEN,x=.8,y=.6)],p,1.4,handedness=['L','R'])
        self.assertFalse(r.web_attack)
        self.assertGreater(r.look_x,0)

    def test_open_palm_wasd_and_release_during_aim(self):
        for x,y,steer,w,s in ((.18,.7,-1,False,False),(.42,.7,1,False,False),(.3,.58,0,True,False),(.3,.82,0,False,True)):
            g,p=self.aim(x=.7)
            aim=palm(TWO,x=.7,y=.6)
            g.update([aim,palm(OPEN,x=.3,y=.7)],p,1.3,handedness=['L','R'])
            r=g.update([aim,palm(OPEN,x=x,y=y)],p,1.4,handedness=['L','R'])
            self.assertEqual((r.steer,r.forward,r.backward),(steer,w,s))
            r=g.update([aim,palm(FIST,x=x,y=y)],p,1.5,handedness=['L','R'])
            self.assertEqual((r.steer,r.forward,r.backward),(0,False,False))

    def test_single_finger_camera_without_aim(self):
        g=Gestures()
        g.update([palm(POINT,x=.5,y=.7)],[],1,handedness=['L'])
        g.update([palm(POINT,x=.5,y=.7)],[],1.12,handedness=['L'])
        r=g.update([palm(POINT,x=.7,y=.7)],[],1.2,handedness=['L'])
        self.assertGreater(r.look_x,0)
        self.assertFalse(r.aim)
        r=g.update([palm(FIST,x=.7,y=.7)],[],1.3,handedness=['L'])
        self.assertEqual(r.look_x,0)

    def test_jump_without_wizard_and_no_jump_after_tracking_loss(self):
        g=Gestures()
        g.update([],body(),1)
        self.assertTrue(g.update([],body(.36),1.1).jump)
        g.update([],[],1.2)
        self.assertFalse(g.update([],body(.3),1.3).jump)


class DoubleInputTests(unittest.TestCase):
    def test_two_distinct_e_down_up_pairs(self):
        b=FakeInput();g=InputGuard(b);g.configure(True)
        g.publish_decision(Decision(aim=True,gadget=True),1)
        for t in (1,1.08,1.16,1.24):g.step(t)
        self.assertEqual([e for e in b.events if e[0]==g.E],[(g.E,True),(g.E,False),(g.E,True),(g.E,False)])

    def test_focus_loss_cancels_second_e(self):
        b=FakeInput();g=InputGuard(b);g.configure(True)
        g.publish_decision(Decision(aim=True,gadget=True),1);g.step(1)
        b.process='notepad.exe';g.step(1.04)
        b.process='spider-man.exe';g.step(1.16)
        self.assertEqual([e for e in b.events if e[0]==g.E],[(g.E,True),(g.E,False)])

    def test_wasd_opposites_release_and_stale_watchdog(self):
        b=FakeInput();g=InputGuard(b);g.configure(True)
        g.publish_decision(Decision(forward=True,steer=-1,aim=True),1);g.step(1)
        self.assertTrue({g.W,g.A}.issubset(g.held))
        g.publish_decision(Decision(backward=True,steer=1,aim=True),1.1);g.step(1.1)
        self.assertTrue({g.S,g.D}.issubset(g.held))
        self.assertFalse({g.W,g.A}&g.held)
        g.step(1.41);self.assertFalse(g.held or g.mouse_held)
