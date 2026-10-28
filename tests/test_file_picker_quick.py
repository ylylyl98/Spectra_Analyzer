"""Real QtQuick.Dialogs contracts without opening an operating-system chooser."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QObject, QTimer, QUrl, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlExpression, QQmlEngine
from PySide6.QtQuick import QQuickWindow
from shiboken6 import delete, isValid

from spectra_app.file_picker import FilePicker, _PickerRequest

APP = QGuiApplication.instance() or QGuiApplication([])


def value(dialog, expression):
    result = QQmlExpression(QQmlEngine.contextForObject(dialog), dialog, expression)
    output = result.evaluate()
    if result.hasError():
        raise AssertionError(result.error().toString())
    return output[0] if isinstance(output, tuple) else output


class QuickFilePickerTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.owner = QQuickWindow()
        self.addCleanup(lambda: delete(self.owner) if isValid(self.owner) else None)
        self.files = [Path(self.folder.name) / name for name in ('ASCII space.dat', '测量 😀.dat')]
        for path in self.files:
            path.write_text('data', encoding='utf-8')
        self.requests = []

    def run_picker(self, call, action):
        def open_request(request):
            self.requests.append(request)
            self.assertIs(request.dialog.property('parentWindow'), self.owner)
            self.assertEqual(request.dialog.property('modality'), Qt.ApplicationModal)
            self.assertEqual(value(request.dialog, 'Number(options)'), 0)
            action(request)
        with patch.object(_PickerRequest, '_open', open_request):
            result = call()
        self.assertFalse(isValid(self.requests[-1]))
        return result

    def test_open_file_real_qml_filters_owner_and_unicode_roundtrip(self):
        filters = 'Data files (*.dat *.DAT);;All files (*)'
        owner = QObject(self.owner)
        def accept(request):
            dialog = request.dialog
            self.assertEqual(value(dialog, 'Number(fileMode)'), 0)
            self.assertEqual(dialog.property('title'), 'Open measurement')
            self.assertEqual(dialog.property('nameFilters'), filters.split(';;'))
            self.assertEqual(value(dialog, 'selectedNameFilter.index'), 1)
            self.assertEqual(dialog.property('currentFolder').toLocalFile(), str(Path(self.folder.name)).replace('\\', '/'))
            self.assertTrue(dialog.setProperty('selectedFile', QUrl.fromLocalFile(str(self.files[1]))))
            dialog.accepted.emit()
        result = self.run_picker(lambda: FilePicker.getOpenFileName(
            owner, 'Open measurement', self.folder.name, filters, 'All files (*)'), accept)
        self.assertEqual(result, (str(self.files[1]).replace('\\', '/'), 'All files (*)'))

    def test_multiple_files_preserve_order_and_selected_filter(self):
        def accept(request):
            self.assertEqual(value(request.dialog, 'Number(fileMode)'), 1)
            # QML supplies QList<QUrl> conversion; a Python QVariant list cannot
            # be assigned to this native property through QQmlProperty.write.
            urls = [QUrl.fromLocalFile(str(p)).toString() for p in reversed(self.files)]
            value(request.dialog, 'currentFiles = ' + json.dumps(urls))
            value(request.dialog, 'selectedNameFilter.index = 1')
            request.dialog.accepted.emit()
        result = self.run_picker(lambda: FilePicker.getOpenFileNames(
            self.owner, filter='All (*);;Data (*.dat)'), accept)
        self.assertEqual(result, ([str(p).replace('\\', '/') for p in reversed(self.files)], 'Data (*.dat)'))

    def test_save_suggested_filename_suffix_and_filter(self):
        target = Path(self.folder.name) / '保存 😀.svg'
        def accept(request):
            dialog = request.dialog
            self.assertEqual(value(dialog, 'Number(fileMode)'), 2)
            self.assertEqual(dialog.property('currentFile').toLocalFile(), str(target).replace('\\', '/'))
            self.assertEqual(dialog.property('defaultSuffix'), 'svg')
            self.assertEqual(value(dialog, 'selectedNameFilter.index'), 0)
            dialog.accepted.emit()
        self.assertEqual(self.run_picker(lambda: FilePicker.getSaveFileName(
            self.owner, 'Save', str(target), 'SVG (*.svg);;PNG (*.png)', default_suffix='svg'), accept),
            (str(target).replace('\\', '/'), 'SVG (*.svg)'))

    def test_folder_real_qml_and_unicode_selection(self):
        target = Path(self.folder.name) / '目录 😀'
        target.mkdir()
        def accept(request):
            # selectedFolder is helper-backed. Offscreen initializes the real
            # Quick fallback without creating an operating-system chooser.
            self.assertEqual(APP.platformName(), 'offscreen')
            request.dialog.open()
            self.assertEqual(request.dialog.property('currentFolder').toLocalFile(), self.folder.name.replace('\\', '/'))
            self.assertTrue(request.dialog.setProperty('selectedFolder', QUrl.fromLocalFile(str(target))))
            request.dialog.accepted.emit()
        self.assertEqual(self.run_picker(lambda: FilePicker.getExistingDirectory(
            self.owner, 'Folder', self.folder.name), accept), str(target).replace('\\', '/'))

    def test_rejection_returns_canonical_empty_even_with_previous_selection(self):
        for method, expected in ((FilePicker.getOpenFileName, ('', '')),
                                 (FilePicker.getOpenFileNames, ([], '')),
                                 (FilePicker.getSaveFileName, ('', '')),
                                 (FilePicker.getExistingDirectory, '')):
            with self.subTest(method=method.__name__):
                self.assertEqual(self.run_picker(lambda: method(self.owner, dir=str(self.files[0])),
                    lambda request: request.dialog.rejected.emit()), expected)

    def test_synchronous_finish_and_delayed_rejection_do_not_hang(self):
        self.assertEqual(self.run_picker(lambda: FilePicker.getOpenFileName(self.owner),
            lambda request: QTimer.singleShot(0, request.dialog.rejected.emit)), ('', ''))
        self.assertEqual(self.run_picker(lambda: FilePicker.getOpenFileName(self.owner),
            lambda request: request.dialog.rejected.emit()), ('', ''))

    def test_owner_destruction_cancels_and_disposes_request_and_engine(self):
        objects = []
        def destroy(request):
            objects.extend((request, request.dialog, request.engine, request.component))
            QTimer.singleShot(0, lambda: delete(self.owner))
        self.assertEqual(self.run_picker(lambda: FilePicker.getOpenFileName(self.owner), destroy), ('', ''))
        self.assertTrue(all(not isValid(obj) for obj in objects))

    def test_window_close_cancels_without_requiring_owner_destruction(self):
        self.owner.show()
        self.assertEqual(self.run_picker(lambda: FilePicker.getOpenFileName(self.owner),
            lambda request: QTimer.singleShot(0, self.owner.close)), ('', ''))
        self.assertTrue(isValid(self.owner))

    def test_open_failure_disposes_all_objects_and_signal_connections(self):
        objects = []
        def fail(request):
            objects.extend((request, request.dialog, request.component, request.engine))
            raise RuntimeError('open failed')
        with patch.object(_PickerRequest, '_open', fail), self.assertRaisesRegex(RuntimeError, 'open failed'):
            FilePicker.getOpenFileName(self.owner)
        self.assertTrue(all(not isValid(obj) for obj in objects))
        APP.aboutToQuit.emit()

    def test_application_shutdown_cancels_and_disconnects_finished_request(self):
        self.assertEqual(self.run_picker(lambda: FilePicker.getOpenFileName(self.owner),
            lambda request: QTimer.singleShot(0, APP.aboutToQuit.emit)), ('', ''))
        APP.aboutToQuit.emit()

    def test_nonlocal_selection_is_never_returned_as_a_filesystem_path(self):
        def accept(request):
            request.dialog.setProperty('selectedFile', QUrl('https://example.com/a.dat'))
            self.assertEqual(request.dialog.property('selectedFile'), QUrl('https://example.com/a.dat'))
            request.dialog.accepted.emit()
        self.assertEqual(self.run_picker(lambda: FilePicker.getSaveFileName(self.owner), accept), ('', ''))

    def test_service_does_not_load_widgets_or_construct_a_proxy_window(self):
        widget_module = sys.modules.get('PySide6.QtWidgets')
        before = widget_module.QApplication.allWidgets() if widget_module and APP.inherits('QApplication') else []
        windows = set(APP.allWindows())
        self.run_picker(lambda: FilePicker.getOpenFileName(self.owner),
                        lambda request: request.dialog.rejected.emit())
        self.assertEqual(set(APP.allWindows()), windows)
        if widget_module is None:
            self.assertNotIn('PySide6.QtWidgets', sys.modules)
            self.assertEqual(APP.metaObject().className(), 'QGuiApplication')
        else:
            self.assertEqual(widget_module.QApplication.allWidgets(), before)


if __name__ == '__main__':
    unittest.main()
