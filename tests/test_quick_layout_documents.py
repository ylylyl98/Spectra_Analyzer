"""Document layout contracts through isolated, real production Quick roots."""
import os
from pathlib import Path
import subprocess
import sys
import unittest

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_quick_application_transactions import ActualTransactions


CASES = ('new_workspace_tabs', 'saved_arrangements', 'legacy_missing_layout',
         'runtime_identity', 'small_workbook')


class QuickLayoutDocumentsTests(unittest.TestCase):
    def test_document_layout_contracts(self):
        for name in CASES:
            with self.subTest(case=name):
                result = subprocess.run(
                    [sys.executable, '-X', 'utf8', str(Path(__file__).resolve()), '--case', name],
                    cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
                    env=dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software'),
                    timeout=90)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stderr, '', result.stderr)
                self.assertIn(name + ': PASS after Quick teardown', result.stdout)


class ActualLayoutDocuments(ActualTransactions):
    def case_new_workspace_tabs(self):
        from unittest.mock import patch
        from spectra_app.common_prompts import PromptAction
        from tests.quick_application_helpers import item, pump
        docs = self.w.graph_documents
        self.assertEqual(docs.mode, 'tabbed')
        self.assertTrue(item(self.host, 'documentsTabbed').property('checked'))
        book = self.book()
        first = ('workbook', book.workbook_id, '')
        self.assertEqual(docs.visible_keys(), (first,))
        docs.setMode('free')
        with patch('spectra_app.main_window.prompt_message', return_value=PromptAction.DISCARD):
            self.assertTrue(self.w.new_project())
        pump()
        self.assertEqual(docs.mode, 'tabbed')
        self.assertEqual(docs.keys(), ())
        self.assertTrue(docs.registry.initial_view)
        self.assertFalse(self.errors)

    def case_saved_arrangements(self):
        from copy import deepcopy
        from spectra_app.project_io import read_project
        from tests.quick_application_helpers import pump
        docs = self.w.graph_documents
        first = self.book()
        second_source = self.folder / 'second.csv'
        second_source.write_text('X,Y\n0,40\n1,50\n2,60\n', encoding='utf-8')
        second = self.w.import_xy_files([second_source])
        keys = [('workbook', owner.workbook_id, '') for owner in (first, second)]
        for arrangement in ('free', 'tile', 'cascade', 'tabbed'):
            with self.subTest(arrangement=arrangement):
                docs.setMode('free')
                pump()
                for index, key in enumerate(keys):
                    frame = docs._presentations[key].frame
                    token = docs.captureFrameInput(list(key), frame)
                    self.assertTrue(docs.moveResize(list(key), frame, token,
                        18 + index * 47, 22 + index * 39, 380 + index * 30, 280 + index * 20))
                if arrangement == 'tile':
                    docs.tile()
                elif arrangement == 'cascade':
                    docs.cascade()
                elif arrangement == 'tabbed':
                    docs.setMode('tabbed')
                pump()
                expected = deepcopy(docs.workspace_snapshot(docs.keys()))
                path = self.w.save_project(self.folder / (arrangement + '.spectra'))
                self.assertEqual(read_project(path).graph_workspace, expected)
                self.assertTrue(self.w.open_project(path))
                pump(100)
                self.assertEqual(docs.workspace_snapshot(docs.keys()), expected)
                self.assertEqual(docs.registry.active_key, tuple(expected['active']))
                self.w._refresh_project_modified()
                self.assertFalse(self.w.projectModified)
        self.assertFalse(self.errors)

    def case_legacy_missing_layout(self):
        from spectra_app.dataset_state import Workspace
        from spectra_app.project_io import write_project
        from tests.quick_application_helpers import pump
        path = self.folder / 'legacy-no-layout.spectra'
        legacy = Workspace()
        self.assertIsNone(legacy.graph_workspace)
        write_project(path, legacy)
        self.assertTrue(self.w.open_project(path))
        pump()
        self.assertEqual(self.w.graph_documents.mode, 'free')
        self.assertFalse(self.errors)

    def case_runtime_identity(self):
        from unittest.mock import patch
        from tests.quick_application_helpers import pump
        book = self.book()
        key = ('workbook', book.workbook_id, '')
        docs = self.w.graph_documents
        controller = docs.document_for(key)
        docs.setMode('free')
        pump()
        frame = docs._presentations[key].frame
        token = docs.captureFrameInput(list(key), frame)
        self.assertTrue(docs.moveResize(list(key), frame, token, 35, 42, 490, 360))
        self.assertTrue(docs.toggleMaximize(list(key), frame, token))
        snapshot = docs.workspace_snapshot(docs.keys())
        parked = docs.park()
        docs.reset_layout()
        docs.restore(parked)
        pump()
        self.assertIs(docs.document_for(key), controller)
        self.assertTrue(docs._presentations[key].maximized)
        self.assertEqual(docs.workspace_snapshot(docs.keys()), snapshot)
        with patch.object(docs, '_finish_close', return_value=False):
            self.assertFalse(docs.close_document(key))
        self.assertIs(docs.document_for(key), controller)
        self.assertTrue(docs._presentations[key].maximized)
        self.assertEqual(docs.workspace_snapshot(docs.keys()), snapshot)
        docs.setMode('tabbed')
        docs.setMode('free')
        pump()
        self.assertEqual(docs.display_rectangle(key), docs.registry.normal_rectangle(key))
        self.assertIs(docs.document_for(key), controller)
        self.assertFalse(self.errors)

    def case_small_workbook(self):
        from PySide6.QtCore import QPointF
        from tests.quick_application_helpers import click, item, pump
        book = self.book()
        key = ('workbook', book.workbook_id, '')
        docs = self.w.graph_documents
        docs.setMode('free')
        pump()
        frame = docs._presentations[key].frame
        token = docs.captureFrameInput(list(key), frame)
        self.assertTrue(docs.moveResize(list(key), frame, token, 0, 0, 240, 180))
        pump(100)
        table = item(self.host, 'workbookSheetTable')
        body = item(self.host, 'sheetCells')
        print('Workbook minimum frame:', frame.width(), frame.height(),
              'table:', table.height(), 'header:', table.property('headerHeight'),
              'data body:', body.width(), body.height(), flush=True)
        self.assertGreaterEqual(body.height(), 2 * table.property('rowHeight'))
        self.assertGreater(body.width(), 0)
        self.assertLessEqual(body.x() + body.width(), table.width())
        viewport = item(self.host, 'workbookContentViewport')
        self.assertLessEqual(table.width(), viewport.width())

        def reveal(target):
            top = target.mapToItem(viewport, QPointF()).y() + viewport.property('contentY')
            viewport.setProperty('contentY', max(0., min(top,
                viewport.property('contentHeight') - viewport.height())))
            pump()
            point = target.mapToItem(viewport, QPointF())
            self.assertGreaterEqual(point.x(), 0.)
            self.assertLessEqual(point.x() + target.width(), viewport.width() + 1,
                target.objectName())
            self.assertGreaterEqual(point.y(), -1.)
            self.assertLessEqual(point.y() + target.height(), viewport.height() + 1)

        for name in ('workbookMappingToggle', 'workbookSnapshot', 'workbookSheetNavigation'):
            reveal(item(self.host, name))
        toggle = item(self.host, 'workbookMappingToggle')
        reveal(toggle)
        click(self.host, toggle)
        self.assertTrue(toggle.property('checked'))
        reveal(item(self.host, 'workbookXColumn'))
        reveal(item(self.host, 'workbookSheetNavigation'))
        reveal(toggle)
        click(self.host, toggle)
        self.assertFalse(toggle.property('checked'))
        frame = docs._presentations[key].frame
        token = docs.captureFrameInput(list(key), frame)
        self.assertTrue(docs.moveResize(list(key), frame, token, 0, 0, 600, 480))
        pump()
        self.assertLessEqual(viewport.property('contentHeight'), viewport.height() + 1)
        self.assertFalse(viewport.property('interactive'))
        self.assertEqual(viewport.property('contentY'), 0.)
        self.host.window.resize(900, 620)
        pump()
        docs.setMode('tabbed')
        pump()
        self.assertGreaterEqual(body.height(), 2 * table.property('rowHeight'))
        for name in ('workbookMappingToggle', 'workbookSnapshot', 'workbookSheetNavigation'):
            reveal(item(self.host, name))
        self.assertFalse(self.errors)


if __name__ == '__main__':
    if '--case' in sys.argv:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        name = sys.argv[sys.argv.index('--case') + 1]
        result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(
            unittest.TestSuite([ActualLayoutDocuments('case_' + name)]))
        if result.wasSuccessful():
            print(name + ': PASS after Quick teardown', flush=True)
        raise SystemExit(0 if result.wasSuccessful() else 1)
    unittest.main()
