"""Units/context and independent face-contact checks with mocked Fusion bounds."""
import importlib.util
import math
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from tribu_exporter.model import StockAllowance


class BladeExtractionTests(unittest.TestCase):
    def setUp(self):
        adsk = ModuleType('adsk')
        adsk.core, adsk.fusion = ModuleType('adsk.core'), ModuleType('adsk.fusion')
        self.measure = NS(getOrientedBoundingBox=Mock())
        adsk.core.Application = NS(get=lambda: NS(measureManager=self.measure))
        adsk.core.Vector3D = NS(create=lambda x,y,z: NS(x=x,y=y,z=z))
        adsk.core.Plane = NS(cast=lambda geometry: geometry)
        name = 'tribu_exporter._blade_extraction_test'
        spec = importlib.util.spec_from_file_location(name,
            Path(__file__).parents[1] / 'tribu_exporter' / 'fusion_extract.py')
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'adsk':adsk,'adsk.core':adsk.core,
                                     'adsk.fusion':adsk.fusion,name:self.module}):
            spec.loader.exec_module(self.module)
        v = self.module.V3
        h = math.sqrt(0.5)
        self.panel_frame = self.module.PanelFrame(v(12,-7,3),v(h,h,0),v(-h,h,0),v(0,0,1))
        self.frame = NS(origin=(30,10,-4),outward_axis=(-0.6,0,0.8),tpa_face_number=7)
        self.reference = (self.panel_frame.origin + self.panel_frame.x_axis.scaled(0.5)
                          + self.panel_frame.y_axis.scaled(1.5) + v(0,0,-0.4))
        self.normal = self.panel_frame.x_axis.scaled(-0.6) + v(0,0,0.8)
        self.measure.getOrientedBoundingBox.return_value = NS(
            centerPoint=self.reference-self.normal.scaled(2),length=4)
        self.face = NS(body=object(),geometry=NS(normal=self.normal),pointOnFace=self.reference,
            boundingBox=NS(minPoint=self.reference-v(10,10,10),maxPoint=self.reference+v(10,10,10)))

    def support(self):
        return self.module._blade_body_support(self.face,self.panel_frame,self.frame,
                                               -20,10,StockAllowance(5,5,5,5))

    def test_translated_rotated_context_and_stock_shift_keep_plane_contact(self):
        body_error, face_error = self.support()
        self.assertAlmostEqual(body_error,0,places=10)
        self.assertAlmostEqual(face_error,0,places=10)
        self.measure.getOrientedBoundingBox.assert_called_once()
        self.assertIs(self.measure.getOrientedBoundingBox.call_args.args[0],self.face.body)

    def test_face_offset_is_detected_even_when_body_support_is_zero(self):
        self.face.pointOnFace = self.reference + self.normal.scaled(0.02)
        body_error, face_error = self.support()
        self.assertAlmostEqual(body_error,0,places=10)
        self.assertAlmostEqual(face_error,0.2,places=10)

    def test_face_tilt_is_bounded_over_full_face_extent(self):
        self.face.geometry.normal = (self.normal + self.panel_frame.y_axis.scaled(0.01)).normalized()
        body_error, face_error = self.support()
        self.assertAlmostEqual(body_error,0,places=10)
        self.assertGreater(face_error,1)

    def test_body_bound_preserves_signed_millimetres_independent_of_face(self):
        self.measure.getOrientedBoundingBox.return_value.centerPoint = self.reference-self.normal.scaled(3)
        body_error, face_error = self.support()
        self.assertAlmostEqual(body_error,-10,places=10)
        self.assertAlmostEqual(face_error,0,places=10)

    def test_unknown_face_geometry_fails_closed(self):
        self.face.geometry = None
        with self.assertRaisesRegex(ValueError,'not planar'):
            self.support()
