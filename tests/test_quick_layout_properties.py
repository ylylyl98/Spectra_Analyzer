"""Properties navigation and retained editors in the actual Quick application."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


CASES = ('xy_routes', 'xy_draft_lifetime', 'publication_reveal', 'context_hints', 'owner_switch')


class QuickLayoutPropertiesTests(unittest.TestCase):
    def run_case(self, name):
        result = subprocess.run(
            [sys.executable, '-X', 'utf8', str(Path(__file__).resolve()), '--case', name],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
            env=dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software'),
            timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, '', result.stderr)
        self.assertIn(name + ': PASS after Quick teardown', result.stdout)


def _wrapper(name):
    def test(self):
        self.run_case(name)
    return test


for _name in CASES:
    setattr(QuickLayoutPropertiesTests, 'test_' + _name, _wrapper(_name))


if '--case' in sys.argv:
    # A frozen production tree can supply RED while another owner edits sources.
    sys.path.insert(0, os.environ.get('SPECTRA_LAYOUT_SOURCE', str(Path(__file__).resolve().parents[1])))
    from tests.test_quick_application_shell import ActualShell

    class ActualProperties(ActualShell):
        def case_xy_routes(self):
            from unittest.mock import patch
            from tests.quick_application_helpers import pump
            page, _, _ = self.xy()
            for task in (0, 1, 2):
                self.w.request_task(task)
                pump()
            for task, name in ((0, 'xyCurveList'), (1, 'xy-analysis-panel'), (2, 'xyField_name')):
                self.click(self.find('task_' + str(task)))
                self.assertEqual((self.w.taskIndex, self.w.inspectorSection), (task, task))
                self.assertTrue(self.find(name).isVisible())
                for other, hidden in ((0, 'xyCurveList'), (1, 'xy-analysis-panel'), (2, 'xyField_name')):
                    if other != task:
                        self.assertFalse(self.find(hidden).isVisible())
            with patch.object(page.canvas, 'draw_idle', wraps=page.canvas.draw_idle) as draw:
                for task in (0, 1, 2, 0):
                    self.click(self.find('task_' + str(task)))
                self.assertEqual(draw.call_count, 0, 'Changing Properties task does not redraw the XY graph')
            for section in (0, 1, 2):
                self.w.selectInspectorSection(section)
                pump()
                self.assertEqual((self.w.taskIndex, self.w.inspectorSection), (section, section))
            self.w.reveal_xy_inspector(page, 0, 'width')
            pump()
            self.assertEqual((self.w.taskIndex, self.w.inspectorSection), (0, 0))
            self.assertTrue(self.find('xyField_width').hasActiveFocus())
            page.edit_x_interval()
            pump()
            self.assertEqual((self.w.taskIndex, self.w.inspectorSection), (2, 2))
            self.assertTrue(self.find('xyChoice_x_range_mode').hasActiveFocus())
            page.analyze_action.trigger()
            pump()
            self.assertEqual((self.w.taskIndex, self.w.inspectorSection), (1, 1))
            self.assertTrue(self.find('xy-analysis-panel').isVisible())

        def case_xy_draft_lifetime(self):
            from PySide6.QtCore import Qt
            from tests.quick_application_helpers import pump
            page, _, curve = self.xy()
            self.w.request_task(0)
            self.w.selectInspectorSection(0)
            pump()
            editor = self.find('xyField_width')
            editor.forceActiveFocus()
            original = editor.property('text')
            self.key(Qt.Key_A, Qt.ControlModifier)
            self.key(Qt.Key_Minus)
            self.assertEqual(editor.property('text'), '-')
            self.click(self.find('task_1'))
            self.click(self.find('task_2'))
            self.click(self.find('task_0'))
            self.assertIs(self.find('xyField_width'), editor)
            self.assertEqual(page.inspector_bridge.values['width'], '-')
            self.assertEqual(curve.width, float(original))
            editor.forceActiveFocus()
            self.key(Qt.Key_Z, Qt.ControlModifier)
            self.assertEqual(editor.property('text'), original)
            self.key(Qt.Key_Y, Qt.ControlModifier)
            self.assertEqual(editor.property('text'), '-')
            self.click(self.find('floatPropertiesButton'))
            self.assertIs(self.find('xyField_width'), editor)
            self.assertTrue(editor.hasActiveFocus())
            self.click(self.find('task_2'))
            self.click(self.find('task_0'))
            self.assertEqual(editor.property('text'), '-')
            editor.forceActiveFocus()
            self.click(self.find('dockPropertiesButton'))
            self.assertIs(self.find('xyField_width'), editor)
            self.assertTrue(editor.hasActiveFocus())
            self.key(Qt.Key_Escape)
            self.assertEqual(editor.property('text'), original)
            self.assertFalse(page.inspector_bridge.hasDraft)
            label = self.find('xyField_label')
            label.forceActiveFocus()
            original_label = label.property('text')
            self.key(Qt.Key_A, Qt.ControlModifier)
            self.key(Qt.Key_X)
            self.click(self.find('task_2'))
            self.click(self.find('task_0'))
            self.assertIs(self.find('xyField_label'), label)
            self.assertEqual(label.property('text'), 'x')
            self.assertEqual(curve.label, original_label)
            label.forceActiveFocus()
            self.key(Qt.Key_Z, Qt.ControlModifier)
            self.assertEqual(label.property('text'), original_label)
            self.key(Qt.Key_Escape)
            self.assertFalse(self.errors)

        def case_publication_reveal(self):
            from tests.quick_application_helpers import pump, wait_until
            path = self.folder / 'layout.dat'
            path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
                f'{1+i/10} {-i-1} {i+1} {10*(i+1)}' for i in range(12)), encoding='utf-8')
            self.w._load_dat_file_direct(path)
            self.w.open_graph_document()
            self.w.request_task(2)
            pump(100)
            page = self.w.plot_page
            before = page.presentation
            self.assertFalse(self.find('formal_page').property('expanded'))
            self.assertFalse(self.find('formal_colorbar').property('expanded'))
            self.assertTrue(self.find('datAppearanceHeading').isVisible())
            self.assertTrue(self.find('publicationFormatHeading').isVisible())
            page.formal.focusRequested.emit('page', 'width_in')
            pump(100)
            editor = self.find('width_in')
            wait_until(editor.hasActiveFocus)
            self.assertTrue(editor.isVisible())
            self.assertTrue(editor.hasActiveFocus(), repr(self.host.window.activeFocusItem()))
            self.assertTrue(self.find('formal_page').property('expanded'))
            self.w.request_task(0)
            pump()
            self.w.request_task(2)
            pump()
            self.assertIs(self.find('width_in'), editor)
            self.assertTrue(self.find('formal_page').property('expanded'))
            self.assertEqual(page.presentation, before)
            self.assertFalse(self.errors)

        def case_context_hints(self):
            from PySide6.QtCore import QPointF
            from tests.quick_application_helpers import pump
            self.w.request_outer_view('workspace')
            self.w.request_task(2)
            pump()
            hint = self.find('propertiesContextHint')
            self.assertTrue(hint.isVisible())
            self.assertIn('Open', hint.property('text'))
            page, _, _ = self.xy()
            self.w.open_workbook(next(iter(self.w.workspace.workbooks)))
            for task in (0, 1, 2):
                self.w.request_task(task)
                pump()
                hint = self.find('propertiesContextHint')
                self.assertTrue(hint.isVisible())
                self.assertTrue(hint.property('text'))
            self.assertIn('graph', hint.property('text').lower())
            path = self.folder / 'source.dat'
            path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
                f'{1+i/10} {i+1} {2*i+2} {3*i+3}' for i in range(13)), encoding='utf-8')
            self.w._load_dat_file_direct(path)
            pump()
            self.w.open_graph_document()
            self.w._workbench_open(('heatmap', self.w._document_key()))
            pump()
            self.assertTrue(self.w.workbenchActive)
            for floating in (False, True):
                self.host.moveProperties(floating)
                if floating:
                    self.host._floating_properties.resize(280, 650)
                for task in (0, 2):
                    self.w.request_task(task)
                    pump()
                    hint = self.find('workbenchPropertiesHint')
                    scroll = self.find('propertiesScroll')
                    self.assertTrue(hint.isVisible())
                    self.assertGreaterEqual(hint.mapToItem(scroll, QPointF()).y(), 0)
                    self.assertLessEqual(hint.mapToItem(scroll, QPointF(0, hint.height())).y(), scroll.height())
                    button = self.find('propertiesBackToWorkspace')
                    self.assertTrue(button.isVisible())
                    self.assertTrue(button.isEnabled())
                    self.assertEqual(button.property('command').key, 'back')
            self.click(button)
            self.assertEqual(self.w.outerView, 'workspace')
            self.assertFalse(self.w.workbenchActive)
            self.assertFalse(self.errors)

        def case_owner_switch(self):
            from tests.quick_application_helpers import pump
            from spectra_app.workbook_state import create_comparison, add_curve
            page, graph, curve = self.xy()
            self.w.request_task(0)
            pump()
            stale_owner = page.inspector_bridge.inputOwner
            second = create_comparison(self.w.workspace, 'Second target')
            add_curve(self.w.workspace, second.comparison_id, curve.sheet_id, curve.x_column, curve.y_column)
            other = self.w.open_xy_comparison(second.comparison_id)
            self.w.request_task(2)
            pump()
            self.assertNotIn(0, page.inspector_bridge._panels)
            self.assertIn(0, other.inspector_bridge._panels)
            self.assertIn('Second target', self.find('propertiesTarget').property('text'))
            self.w.reveal_xy_inspector(page, 0, 'width')
            pump()
            self.assertEqual(self.w.taskIndex, 2)
            self.assertIs(self.w.propertiesController, other)
            self.assertFalse(page.inspector_bridge.draftFor('width', '9', stale_owner))
            self.assertEqual(curve.width, 1.5)
            self.w.open_xy_comparison(graph.comparison_id)
            self.w.request_task(0)
            pump()
            self.assertNotIn(0, other.inspector_bridge._panels)
            self.assertIn(0, page.inspector_bridge._panels)
            self.assertIn(graph.name, self.find('propertiesTarget').property('text'))
            self.assertEqual((self.w.taskIndex, self.w.inspectorSection), (0, 0))
            page.inspector_bridge.setDraft('width', '-')
            self.w.request_task(2)
            with self.assertRaises(ValueError):
                self.w.open_xy_comparison(second.comparison_id)
            pump()
            self.assertIs(self.w.propertiesController, page)
            self.assertEqual((self.w.taskIndex, self.w.inspectorSection), (0, 0))
            self.assertTrue(self.find('xyField_width').hasActiveFocus())
            self.assertEqual(page.inspector_bridge.values['width'], '-')
            page.revert_editor()
            self.assertFalse(self.errors)

    name = sys.argv[sys.argv.index('--case') + 1]
    suite = unittest.TestSuite([ActualProperties('case_' + name)])
    result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(suite)
    if result.wasSuccessful():
        print(name + ': PASS after Quick teardown', flush=True)
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == '__main__':
    unittest.main()
