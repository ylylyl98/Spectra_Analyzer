"""Routine plot edits stay quiet while UI and plot text share Arial."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from dataclasses import replace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import numpy as np
from matplotlib import rc_context
from matplotlib.figure import Figure
from matplotlib.font_manager import FontProperties, findfont
from matplotlib.mathtext import MathTextParser
from PySide6.QtCore import QObject, QPoint, QPointF
from PySide6.QtGui import QFontInfo, QGuiApplication
from PySide6.QtTest import QTest
from tests import quick_migration_case as migration
from tests import ordinary_analysis_quick_helpers as oa

from run_qt import configure_application_fonts
from spectra_app.dataset_state import Workspace
from spectra_app.plot_presentation import PlotAnnotation
from spectra_app.svg_export import render_svg_image
from spectra_app.workbook_state import create_workbook, register_xy_sheet, create_comparison, add_curve
from spectra_app.xy_plot_page import draw_comparison
from tests import dat_properties_quick_helpers as dp
from tests.background_helpers import wait_jobs as wait_for_background_jobs
from tests.formal_quick_helpers import click_item, item, pump, replace_text


class QuietFeedbackFontTests(migration.QuickMigrationCase):
    def setUp(self):
        super().setUp()
        app = QGuiApplication.instance()
        previous_font = app.font()
        self.addCleanup(app.setFont, previous_font)
        font_context = rc_context()
        font_context.__enter__()
        self.addCleanup(font_context.__exit__, None, None, None)
        configure_application_fonts(app)
        self.window = self.controller
        path = self.folder / 'quiet.dat'
        path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
            f'{1+i/10} {i+1} {2*(i+1)} {3*(i+1)}' for i in range(12)), encoding='utf-8')
        self.window._load_dat_file_direct(path)
        self.window.open_graph_document()
        self.window.dat_details_action.setChecked(True)
        self.window.request_task(2)
        self.window.root_window.show()
        pump()

    def tearDown(self):
        wait_for_background_jobs(self.window)
        self.assertEqual(self.quick_errors, [], 'Unexpected production Quick error')

    def test_view_range_render_and_undo_stay_quiet(self):
        w = self.window
        self.assertEqual(w.statusText, '')
        w.dat_properties.draftNumber('xmin', '1.1', w.dat_properties.ownerToken)
        self.assertTrue(w.dat_properties.commitNumber('xmin', w.dat_properties.ownerToken))
        w._render_plot_page()
        self.assertIsNotNone(w.plot_page.rendered_page, w.plot_page.status_text)
        self.assertEqual(w.plot_page.status_text, '')
        self.assertFalse(item(w, 'formal_status').isVisible())
        self.assertTrue(w.undo_edit())
        self.assertEqual(w.statusText, '')
        w._render_plot_page()
        self.assertFalse(item(w, 'formal_status').isVisible())

    def test_render_failure_reveals_footer_and_recovery_hides_it(self):
        w = self.window
        w._render_plot_page()
        self.assertFalse(item(w, 'formal_status').isVisible())
        w._invalidate_plot_page(schedule=False)
        with patch('spectra_app.main_window.render_hybrid_svg', side_effect=ValueError('layout too small')):
            w._render_plot_page()
        self.assertIn('layout too small', w.plot_page.status_text)
        self.assertTrue(item(w, 'formal_status').isVisible())
        self.assertIn('layout too small', item(w, 'formal_status').property('text'))
        self.assertFalse(w.plot_page.command_enabled('export_svg'))
        self.assertFalse(w.plot_page.command_enabled('export_png'))
        w._render_plot_page()
        self.assertIsNotNone(w.plot_page.rendered_page, w.plot_page.status_text)
        self.assertFalse(item(w, 'formal_status').isVisible())
        self.assertTrue(w.plot_page.command_enabled('export_svg'))

    def test_ui_plot_and_exports_enforce_arial_for_custom_font_records(self):
        w = self.window
        app = QGuiApplication.instance()
        self.assertEqual(app.font().family(), 'Arial')
        menu = next(control for control in w.root_window.findChildren(QObject)
                    if control.inherits('QQuickMenu'))
        self.assertEqual(menu.property('font').family(), 'Arial')
        control = dp.reveal(w, 'xmin')
        point = control.mapToScene(QPointF(control.width()/2, control.height()/2)).toPoint()
        QTest.mouseMove(w.root_window, point)
        pump(750)
        tooltips = [control for owner in (w.root_window, self.host.engine)
                    for control in owner.findChildren(QObject)
                    if control.inherits('QQuickToolTip') and control.property('visible')]
        self.assertTrue(tooltips, 'Actual DAT tooltip did not appear')
        for tooltip in tooltips:
            self.assertEqual(tooltip.property('font').family(), 'Arial')
        QTest.mouseMove(w.root_window, point + QPoint(120, 0))
        oa.reveal(w)
        summary = item(w, 'ordinarySummary')
        self.window.request_task(2)
        pump()
        for font in (dp.dat_item(w, 'xmin').property('font'), summary.property('font')):
            self.assertEqual(font.family(), 'Arial')
            self.assertEqual(QFontInfo(font).family(), 'Arial')
        status_text = item(w, 'formal_status').childItems()[0]
        self.assertEqual(status_text.property('font').family(), 'Arial')
        self.assertEqual(QFontInfo(status_text.property('font')).family(), 'Arial')
        self.assertEqual(QFontInfo(summary.property('font')).family(), 'Arial')
        self.assertEqual(w._spec_ax.xaxis.label.get_fontfamily()[0], 'Arial')
        math_text = MathTextParser('path').parse(r'$x + \mathrm{PL} + \alpha$', dpi=72)
        self.assertTrue(math_text.glyphs)
        self.assertTrue(all(glyph[0].family_name == 'Arial' for glyph in math_text.glyphs))

        workspace = Workspace()
        book = create_workbook(workspace)
        sheet = register_xy_sheet(workspace, book.workbook_id, 'Source',
            np.array([[1., 2.], [2., 4.]]), ['Time', 'Signal'])
        comparison = create_comparison(workspace)
        add_curve(workspace, comparison.comparison_id, sheet.sheet_id, 0, 1)
        axes = draw_comparison(Figure(), workspace, comparison)
        for artist in (axes.title, axes.xaxis.label, axes.yaxis.label, axes.get_legend().get_texts()[0]):
            self.assertEqual(artist.get_fontfamily()[0], 'Arial')
            self.assertEqual(FontProperties(fname=findfont(artist.get_fontproperties())).get_name(), 'Arial')

        chosen = replace(w.workspace.active.presentation, font_family='DejaVu Serif',
                         annotations=(PlotAnnotation('font-note', text='Arial note'),))
        w.set_plot_presentation(chosen)
        w._render_plot_page()
        self.assertIsNotNone(w.plot_page.rendered_page, w.plot_page.status_text)
        self.assertEqual(w.workspace.active.presentation.font_family, 'Arial')
        for artist in (w._heat_ax.xaxis.label, w._heat_ax.yaxis.label, w._spec_ax.xaxis.label,
                       *w._heat_ax.get_xticklabels(), *w._spec_ax.get_yticklabels()):
            self.assertEqual(artist.get_fontfamily()[0], 'Arial')
            self.assertEqual(FontProperties(fname=findfont(artist.get_fontproperties())).get_name(), 'Arial')
        w.plot_page.formal.focusRequested.emit('page', 'font_family')
        pump(60)
        font_note = item(w, 'font_family')
        self.assertEqual(font_note.property('text'), 'Font: Arial')
        self.assertIsNone(font_note.property('readOnly'), 'The font is a display note, with no editor')
        click_item(w, 'font_family')
        replace_text(w.root_window, 'Times New Roman')
        self.assertEqual(font_note.property('text'), 'Font: Arial')
        # A programmatic restore cannot turn the display-only field into a
        # second font setting or create a project draft.
        w.plot_page.formal.setDraft('font_family', 'Times New Roman')
        w.plot_page.formal.setValue('font_family', 'Times New Roman')
        self.assertFalse(w.plot_page.has_pending_project_edits())
        w.plot_page.finish_editor()
        self.assertEqual(font_note.property('text'), 'Font: Arial')
        self.assertEqual(w.plot_page.presentation.font_family, 'Arial')

        page = w.plot_page.rendered_page
        self.assertEqual(page.font_family, 'Arial')
        root = ET.fromstring(page.svg_bytes)
        texts = root.findall('.//{http://www.w3.org/2000/svg}text')
        self.assertTrue(texts)
        self.assertIn('Arial note', ''.join(root.itertext()))
        for text in texts:
            self.assertIn('Arial', text.attrib['style'])
            self.assertNotIn('DejaVu Serif', text.attrib['style'])
        self.assertFalse(render_svg_image(page, dpi=72).isNull())


if __name__ == '__main__':
    unittest.main()
