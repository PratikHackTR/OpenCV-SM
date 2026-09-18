import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
import threading

import numpy as np
from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from webmotion.regions_ui import RegionCanvas, RegionsDialog
from webmotion.regions import load_regions
from test_motion import REGIONS


class RegionUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_drag_respects_letterbox_and_can_replace_a_rectangle(self):
        canvas = RegionCanvas({})
        canvas.resize(800,600)
        canvas.image = QImage(800,400,QImage.Format.Format_RGB888)
        canvas.show()
        self.app.processEvents()
        self.assertIsNone(canvas.normalized(QPointF(30,30)))
        QTest.mousePress(canvas,Qt.MouseButton.LeftButton,pos=QPoint(80,140))
        QTest.mouseMove(canvas,QPoint(240,260))
        QTest.mouseRelease(canvas,Qt.MouseButton.LeftButton,pos=QPoint(240,260))
        for actual, expected in zip(canvas.regions['left'],[.1,.1,.3,.4]):
            self.assertAlmostEqual(actual,expected)
        QTest.mousePress(canvas,Qt.MouseButton.LeftButton,pos=QPoint(320,300))
        QTest.mouseRelease(canvas,Qt.MouseButton.LeftButton,pos=QPoint(160,180))
        for actual, expected in zip(canvas.regions['left'],[.2,.2,.4,.5]):
            self.assertAlmostEqual(actual,expected)
        canvas.close()

    def test_save_cancel_and_camera_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            pipeline = SimpleNamespace(regions={}, regions_path=Path(folder)/'regions.json',
                stop_event=threading.Event(), error='',
                frames=SimpleNamespace(get=lambda: (1,np.zeros((480,640,3),dtype=np.uint8))))
            dialog = RegionsDialog(pipeline)
            dialog.refresh()
            self.assertFalse(dialog.save.isEnabled())
            dialog.canvas.regions = dict(REGIONS)
            dialog.refresh()
            self.assertTrue(dialog.save.isEnabled())
            dialog.commit()
            self.assertEqual(pipeline.regions,REGIONS)
            self.assertEqual(load_regions(pipeline.regions_path),REGIONS)
            dialog = RegionsDialog(pipeline)
            dialog.canvas.regions = {}
            dialog.reject()
            self.assertEqual(pipeline.regions,REGIONS)
            dialog = RegionsDialog(pipeline)
            pipeline.stop_event.set()
            dialog.refresh()
            self.assertFalse(dialog.save.isEnabled())
            dialog.reject()
