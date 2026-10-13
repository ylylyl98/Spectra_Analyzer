from dataclasses import replace
import importlib.util
from types import SimpleNamespace
import unittest
from spectra_app.plot_presentation import HeatmapSnapshot, PlotPresentation


class PlotAnnotationGeometryTests(unittest.TestCase):
    def test_original_data_geometry_exchanges_flips_and_zoom_uses_final_axes_bounds(self):
        self.assertIsNotNone(importlib.util.find_spec('spectra_app.plot_annotations'),
                             'Annotation coordinate conversion is missing')
        from spectra_app.plot_annotations import coordinates_to_page, page_to_coordinates
        snapshot = HeatmapSnapshot([10.,20.],[1.,3.],[[1.,2.],[3.,4.]])
        p = PlotPresentation()
        p = replace(p, axis=replace(p.axis, exchange_xy=True,flip_x=True))
        page = SimpleNamespace(axes_bounds=(.2,.1,.6,.7), display_x_limits=(3.,1.),
                               display_y_limits=(10.,20.))
        # Original x=12.5,y=2.5 is displayed at fraction (.25,.25).
        point = coordinates_to_page((12.5,2.5),(1000,800),page,snapshot,p,'data')
        self.assertAlmostEqual(point[0],350.)
        self.assertAlmostEqual(point[1],580.)
        self.assertEqual(page_to_coordinates(point,(1000,800),page,snapshot,p,'data'),(12.5,2.5))
        zoomed = coordinates_to_page((12.5,2.5),(2000,1600),page,snapshot,p,'data')
        self.assertAlmostEqual(zoomed[0],700.)
        self.assertAlmostEqual(zoomed[1],1160.)
        fraction=page_to_coordinates(zoomed,(2000,1600),page,snapshot,p,'axes-fraction')
        self.assertAlmostEqual(fraction[0],.25)
        self.assertAlmostEqual(fraction[1],.25)

    def test_log_coordinate_geometry_keeps_original_data_units(self):
        self.assertIsNotNone(importlib.util.find_spec('spectra_app.plot_annotations'),
                             'Annotation log-coordinate conversion is missing')
        from spectra_app.plot_annotations import coordinates_to_page, page_to_coordinates
        snapshot=HeatmapSnapshot([1.,100.],[10.,20.],[[1.,2.],[3.,4.]],x_scale='log')
        page=SimpleNamespace(axes_bounds=(0.,0.,1.,1.),display_x_limits=(1.,100.),display_y_limits=(10.,20.))
        point=coordinates_to_page((10.,15.),(600,400),page,snapshot,PlotPresentation(),'data')
        self.assertEqual(point,(300.,200.))
        result=page_to_coordinates(point,(600,400),page,snapshot,PlotPresentation(),'data')
        self.assertAlmostEqual(result[0],10.)
        self.assertEqual(result[1],15.)


if __name__=='__main__':
    unittest.main()
