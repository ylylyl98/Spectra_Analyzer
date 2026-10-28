"""Production formal controllers in actual QML with QGuiApplication only.

Run separately from QWidget test modules; these tests intentionally use QGuiApplication.
"""
from dataclasses import replace
import os
from pathlib import Path
import unittest
import subprocess
import sys
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPointF, Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest

from spectra_app.formal_plot_controller import FormalPlotController
from spectra_app.formal_preview_scene import FormalPreviewScene
from spectra_app.plot_presentation import PlotAnnotation, PlotPresentation
from tests.test_formal_preview_controller import page, OWNER, pump

_CHILD = os.environ.get('SPECTRA_FORMAL_PLOT_QGUI_CHILD') == '1'
APP = (QGuiApplication.instance() or QGuiApplication([])) if _CHILD else None
QML = Path(__file__).resolve().parents[1] / 'spectra_app/qml'


class FormalPlotControllerTests(unittest.TestCase):
    def setUp(self):
        self.engine = QQmlEngine()
        self.window = QQuickWindow()
        self.window.resize(1320, 850)
        self.window.show()
        self.controllers, self.roots, self.attachments, self.components = [], [], [], []
        pump()

    def tearDown(self):
        for controller in self.controllers:
            controller.dispose()
        for root in self.roots:
            root.setParentItem(None)
            root.deleteLater()
        self.window.close()
        self.window.deleteLater()
        for component in self.components:
            component.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        pump()
        self.engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def controller(self, color='red'):
        c = FormalPlotController(QObject(self.engine), prompt_window=self.window)
        self.controllers.append(c)
        note = PlotAnnotation('note', text='Retained note', position=(.3, .5))
        c.set_presentation(PlotPresentation(width_in=6., height_in=4., annotations=(note,)))
        c.invalidate(OWNER)
        c.preview.set_geometry(SimpleNamespace(owner_token=OWNER, title='', x_scale='linear',
            y_scale='linear', x_label='X', y_label='Y'), c.presentation)
        self.assertTrue(c.commit_rendered_page(page(color)))
        return c

    def root(self, filename, bridge, x=0, y=0, width=600, height=60, **properties):
        component = QQmlComponent(self.engine, QUrl.fromLocalFile(str(QML / filename)))
        self.components.append(component)
        self.assertEqual(component.status(), QQmlComponent.Ready,
                         '\n'.join(e.toString() for e in component.errors()))
        root = component.createWithInitialProperties({'bridge': bridge, **properties})
        self.assertIsNotNone(root, '\n'.join(e.toString() for e in component.errors()))
        root.setParentItem(self.window.contentItem())
        root.setPosition(QPointF(x, y))
        root.setWidth(width)
        root.setHeight(height)
        self.roots.append(root)
        pump()
        return root

    def mount(self, c, x=0):
        commands = self.root('FormalCommands.qml', c.formal, x=x)
        properties = self.root('FormalProperties.qml', c.formal, x=x, y=90, width=290, height=760)
        attachment = FormalPreviewScene(c.preview, self.engine)
        self.attachments.append(attachment)
        preview = self.root('FormalPreview.qml', c.preview, x=x+300, y=90, width=300, height=400,
                            attachmentGeneration=attachment.generation)
        attachment.bind_item(preview)
        pump()
        return commands, properties, preview, attachment

    def item(self, root, name):
        def find(item):
            if item.objectName() == name:
                return item
            for child in item.childItems():
                result = find(child)
                if result is not None:
                    return result
        result = find(root)
        self.assertIsNotNone(result, name)
        return result

    def click(self, root, name):
        item = self.item(root, name)
        pos = item.mapToScene(QPointF(item.width()/2, item.height()/2)).toPoint()
        self.assertTrue(self.window.geometry().size().width() > pos.x() >= 0)
        QTest.mouseClick(self.window, Qt.LeftButton, pos=pos)
        pump()
        return item

    def edit(self, c, root, field, text):
        c.formal.focusRequested.emit('page', field)
        pump(50)
        editor = self.click(root, field)
        QTest.keyClick(self.window, Qt.Key_A, Qt.ControlModifier)
        for char in text:
            QTest.keyClick(self.window, ord(char.upper()))
        pump()
        self.assertEqual(editor.property('text'), text)
        return editor

    def test_qgui_only_same_engine_real_edit_command_and_retained_scene_state(self):
        self.assertIs(type(APP), QGuiApplication)
        a, b = self.controller(), self.controller('blue')
        for c in (a, b):
            for name in ('host', 'isEnabled', 'isVisible', 'update', 'window', 'rootObject', 'setSource', 'addAction'):
                self.assertFalse(hasattr(c, name), name)
        commands_a, properties_a, preview_a, attachment_a = self.mount(a)
        commands_b, properties_b, preview_b, attachment_b = self.mount(b, 660)
        self.assertIsNone(self.engine.rootContext().contextProperty('appBridge'))
        for commands, properties, c in ((commands_a, properties_a, a), (commands_b, properties_b, b)):
            self.assertIs(commands.property('bridge'), c.formal)
            self.assertIs(properties.property('bridge'), c.formal)
        self.assertNotEqual(a.preview.imageUrl, b.preview.imageUrl)
        exports_a, exports_b, changes_a, changes_b = [], [], [], []
        a.exportRequested.connect(exports_a.append)
        b.exportRequested.connect(exports_b.append)
        a.presentationChanged.connect(changes_a.append)
        b.presentationChanged.connect(changes_b.append)
        self.click(commands_a, 'formal_export_svg')
        self.assertEqual((exports_a, exports_b), (['svg'], []))
        self.edit(a, properties_a, 'width_in', '5.12345678')
        QTest.keyClick(self.window, Qt.Key_Return)
        pump()
        self.assertEqual(a.presentation.width_in, 5.12345678)
        self.assertEqual(b.presentation.width_in, 6.)
        self.assertEqual((len(changes_a), len(changes_b)), (1, 0))
        a.select_annotation('note')
        a.set_zoom('150%')
        self.edit(a, properties_a, 'width_in', '-')
        with self.assertRaises(ValueError):
            a.finish_editor()
        accepted = a.preview.svg_bytes
        # Destroy the actual roots and rebuild them in the same engine.
        for old in (commands_a, properties_a, preview_a):
            self.roots.remove(old)
            old.setParentItem(None)
            old.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        pump()
        self.assertFalse(a.preview._disposed)
        commands_a, properties_a, preview_a, remount = self.mount(a)
        self.assertNotEqual(attachment_a.route, remount.route)
        self.assertEqual(self.item(properties_a, 'width_in').property('text'), '-')
        self.assertEqual(a.formal._selected_annotation, 'note')
        self.assertEqual(a.preview.selected_id, 'note')
        self.assertEqual(a.preview.svg_bytes, accepted)
        self.assertEqual(a.zoom_text, '150%')
        with self.assertRaises(ValueError):
            a.finish_editor()
        self.assertEqual(b.preview.svg_bytes, page('blue').svg_bytes)
        self.assertFalse(b.has_pending_project_edits())
        capture = self.window.grabWindow()
        self.assertFalse(capture.isNull())
        destination = os.environ.get('SPECTRA_FORMAL_PLOT_CAPTURE')
        if destination:
            self.assertTrue(capture.save(destination))

    def test_projection_does_not_authorize_but_real_preview_input_and_command_do(self):
        c = self.controller()
        commands, properties, preview, attachment = self.mount(c)
        authorizations = []
        c.set_input_guard(lambda: authorizations.append('activate') or True)
        c.preview.canEdit
        c.preview.overlay
        c.formal.enabled
        c.formal.state
        c.formal.sync()
        pump()
        self.assertEqual(authorizations, [])
        c.formal.command('export_svg')
        self.assertGreater(len(authorizations), 0)
        authorizations.clear()
        note = c.presentation.annotations[0]
        paper = self.item(preview, 'formalPaper')
        pos = paper.mapToScene(QPointF(c.preview.annotation_point(note))).toPoint()
        QTest.mouseClick(self.window, Qt.LeftButton, pos=pos)
        pump()
        self.assertGreater(len(authorizations), 0)
        self.assertEqual(c.formal._selected_annotation, 'note')
        c.set_input_guard(lambda: True)
        self.assertEqual(c.preview.selected_id, 'note')
        c.set_enabled(False)
        c.set_enabled(True)
        self.assertEqual(c.preview.selected_id, 'note')

    def test_hidden_system_validation_does_not_activate_and_all_drafts_validate_atomically(self):
        c = self.controller()
        c.select_annotation('note')
        c.formal.setDraft('annotation_x', '-')
        c.formal.setDraft('width_in', '5.12345678')
        before = c.presentation
        activations = []
        c.set_input_guard(lambda: activations.append(True) or False)
        with self.assertRaises(ValueError):
            c.finish_editor()
        self.assertEqual(c.presentation, before)
        self.assertEqual(c.formal.state['fields']['width_in'], '5.12345678')
        self.assertEqual(activations, [])
        c.set_input_guard(lambda: True)
        c.formal.setDraft('annotation_x', '.25')
        c.set_input_guard(lambda: activations.append(True) or False)
        c.finish_editor()
        self.assertEqual(c.presentation.width_in, 5.12345678)
        self.assertEqual(c.presentation.annotations[0].position[0], .25)
        self.assertEqual(activations, [])

    def test_real_window_prompt_once_cancel_replacement_owner_guard_and_disposal(self):
        c = self.controller()
        c.formal.openPrompt('text')
        first = c.formal._annotation_dialog
        self.assertIs(first.window().transientParent(), self.window)
        first.bridge.setText('Accepted once')
        first.accept()
        first.accept()
        self.assertEqual([a.text for a in c.presentation.annotations], ['Retained note', 'Accepted once'])
        c.formal.openPrompt('arrow')
        cancel = c.formal._annotation_dialog
        cancel.reject()
        self.assertEqual(len(c.presentation.annotations), 2)
        c.formal.openPrompt('text')
        old = c.formal._annotation_dialog
        c.formal.openPrompt('arrow')
        replacement = c.formal._annotation_dialog
        old.bridge.setText('Stale text')
        old.accepted.emit()
        old.rejected.emit()
        self.assertTrue(c.formal._prompt['open'])
        self.assertEqual(c.formal._prompt['kind'], 'arrow')
        self.assertEqual(c.formal._prompt['text'], '')
        replacement.reject()
        for transition in ('owner', 'guard', 'dispose'):
            c.formal.openPrompt('text')
            old = c.formal._annotation_dialog
            original = c.presentation
            if transition == 'owner':
                c.invalidate(('replacement', 1, 2, 3, 4))
            elif transition == 'guard':
                c.set_input_guard(lambda: True)
            else:
                c.dispose()
            old.bridge.setText('Stale after ' + transition)
            old.accepted.emit()
            self.assertEqual(c.presentation, original)
            if transition == 'owner':
                c.invalidate(OWNER)
                self.assertTrue(c.commit_rendered_page(page()))
        self.assertTrue(c.retired)
        self.assertFalse(c.commit_rendered_page(page()))
        self.assertFalse(any(v['enabled'] for v in c.formal.state['commands'].values()))
        self.assertFalse(c.preview.canEdit)

    def test_controller_owner_deleted_before_live_scene_retires_route_and_prompt(self):
        c = self.controller()
        attachment = FormalPreviewScene(c.preview, self.engine)
        self.attachments.append(attachment)
        root = self.root('FormalPreview.qml', c.preview, width=400, height=300,
                         attachmentGeneration=attachment.generation)
        attachment.bind_item(root)
        c.formal.openPrompt('text')
        dialog = c.formal._annotation_dialog
        rejected = []
        dialog.rejected.connect(lambda: rejected.append(True))
        owner = c.parent()
        self.controllers.remove(c)
        owner.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        pump()
        self.assertTrue(attachment._retired)
        self.assertIsNone(self.engine.imageProvider(attachment.route))
        self.assertEqual(rejected, [True])
        # Real item/window teardown after owner deletion must not call dead state.
        root.setParentItem(None)
        root.deleteLater()
        self.roots.remove(root)
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        pump()

    def test_guard_callback_owner_change_rejects_pending_action_and_prompt_request(self):
        c = self.controller()
        exports = []
        c.exportRequested.connect(exports.append)
        def rebind():
            c.set_input_guard(lambda: True)
            return True
        c.set_input_guard(rebind)
        c.formal.command('export_svg')
        self.assertEqual(exports, [])
        def changes_owner_directly():
            c.expected_owner = ('different-owner',)
            return True
        c.set_input_guard(changes_owner_directly)
        c.formal.command('export_svg')
        self.assertEqual(exports, [])
        c.expected_owner = OWNER
        c.set_input_guard(lambda: True)
        c.formal.openPrompt('text')
        dialog = c.formal._annotation_dialog
        original = c.presentation
        # Changing ownership during the authorization call must reject even
        # though the newly installed guard itself authorizes new input.
        c.formal._input_guard = rebind
        dialog.accept()
        self.assertEqual(c.presentation, original)
        self.assertFalse(c.formal._prompt['open'])


class FormalPlotControllerTestsSubprocessTests(unittest.TestCase):
    def test_isolated_qgui_production_controller_and_scenes(self):
        result = subprocess.run(
            [sys.executable, '-X', 'utf8', '-m', 'unittest', 'tests.test_formal_plot_controller', '-v'],
            cwd=Path(__file__).resolve().parents[1],
            env=dict(os.environ, QT_QPA_PLATFORM='offscreen', SPECTRA_FORMAL_PLOT_QGUI_CHILD='1'),
            capture_output=True, text=True, encoding='utf-8', timeout=90)
        # Preserve complete child evidence in the invoking test runner's log.
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Ran 6 tests', result.stderr)
        self.assertIn('OK', result.stderr)


def load_tests(loader, tests, pattern):
    case = FormalPlotControllerTests if _CHILD else FormalPlotControllerTestsSubprocessTests
    return loader.loadTestsFromTestCase(case)


if __name__ == '__main__':
    unittest.main()
