"""Real Quick input at the shared Properties frontend/typed bridge boundary.

The fixture owns drafts just as the production bridge does. Scientific state,
history and import behavior are covered separately against MainWindow.
"""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from copy import deepcopy
import unittest

from PySide6.QtCore import QObject, Property, Signal, Slot, QPointF, Qt, qInstallMessageHandler
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from spectra_app.quick_ui import QuickPanel
from tests.formal_quick_helpers import click_item, item, pump

APP = QApplication.instance() or QApplication([])


class PropertiesFixture(QObject):
    stateChanged = Signal()
    draftChanged = Signal()
    focusRequested = Signal(str, str)
    revealRequested = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.token = 'dataset-a:graph-a'
        self.calls = []
        self.error_field = self.error_text = ''
        self.data = {
            **{key: '0.0000' for key in ('xmin', 'ymin', 'vmin', 'edge0')},
            **{key: '1.0000' for key in ('xmax', 'ymax', 'vmax', 'edge1')},
            **{key + '_lock': False for key in ('xmin', 'xmax', 'ymin', 'ymax', 'vmin', 'vmax')},
            'linthresh': '1.00000000', 'linthresh_enabled': False,
            'regional': False, 'edge_count': 0, 'edge_unit': 'meV',
            'fix_regions': False, 'fix_region_edges': False,
            'colormap': 'RdBu_r', 'mapping': 'Linear', 'color_scale': 'Global',
            'colormaps': [{'label': name, 'value': name} for name in
                          ('jet', 'RdBu_r', 'RdBu_r_0.6', 'turbo', 'viridis', 'plasma',
                           'inferno', 'magma', 'cividis', 'RdBu_r_p0p45')],
            'mappings': [{'label': name, 'value': name} for name in ('Linear', 'Log', 'SymLog', 'Zero-centered')],
            'color_scales': [{'label': name, 'value': name} for name in ('Global', '2 regions', '3 regions')],
            'axis_override': 'auto', 'source_axis_enabled': True,
            'axis_overrides': [{'label': name, 'value': name} for name in ('auto', 'eV', 'meV', 'nm', 'cm^-1', 'index', 'unknown')],
            'axis_status': 'Detected eV from source header.', 'x_label': 'Energy (eV)', 'y_label': 'Gate (V)',
            'plot_title': 'Before', 'y_axis_name': 'Gate', 'y_axis_unit': 'V',
            'signal_title': 'Signal', 'signal_title_auto': False,
            'signal_presets': [{'label': name, 'value': name} for name in ('Signal', 'PL (arb. units)')],
            'fix_annotations': True, 'template_active': True,
            'title_preset': 'Current group', 'title_presets': ['Manual', 'Current group', 'Built-in'],
            'title_pattern': 'E={voltage}', 'title_angle_map': '', 'title_preset_name': '',
            'title_fields': ['voltage', 'field', 'angle', 'valley', 'temperature', 'sample'],
            'title_template_status': 'Preview', 'disclosures': {},
        }
        self.owners = {self.token: self.data}

    state = Property('QVariantMap', lambda self: self.data, notify=stateChanged)
    ownerToken = Property(str, lambda self: self.token, notify=stateChanged)
    enabled = Property(bool, lambda self: True, notify=stateChanged)
    errorField = Property(str, lambda self: self.error_field, notify=draftChanged)
    errorText = Property(str, lambda self: self.error_text, notify=draftChanged)

    def publish(self):
        self.stateChanged.emit()
        self.draftChanged.emit()

    def owner(self, token):
        return token == self.token

    @Slot(str, str, str)
    def draftNumber(self, field, text, token):
        self.calls.append(('draftNumber', field, text, token))
        if self.owner(token):
            self.data[field] = text
            self.publish()

    @Slot(str, str, result=bool)
    def commitNumber(self, field, token):
        self.calls.append(('commitNumber', field, token))
        if not self.owner(token):
            return False
        try:
            value = float(self.data[field])
        except ValueError:
            self.error_field, self.error_text = field, 'Enter a finite number.'
            self.publish()
            self.focusRequested.emit('colors' if field.startswith(('v', 'edge')) else 'ranges', field)
            return False
        self.data[field] = f'{value:.4f}'
        self.error_field = self.error_text = ''
        self.publish()
        return True

    @Slot(str, int, str, result=bool)
    def stepNumber(self, field, steps, token):
        self.calls.append(('stepNumber', field, steps, token))
        if not self.owner(token):
            return False
        self.data[field] = f'{float(self.data[field]) + steps * .01:.4f}'
        self.publish()
        return True

    @Slot(str, bool, str, result=bool)
    def setFlag(self, field, checked, token):
        self.calls.append(('setFlag', field, checked, token))
        if not self.owner(token):
            return False
        self.data[field] = checked
        self.publish()
        return True

    @Slot(str, str, str, result=bool)
    def setChoice(self, field, value, token):
        self.calls.append(('setChoice', field, value, token))
        if not self.owner(token):
            return False
        self.data[field] = value
        if field == 'color_scale':
            self.data['regional'] = value != 'Global'
            self.data['edge_count'] = 2 if value == '3 regions' else 1 if value == '2 regions' else 0
        self.publish()
        return True

    @Slot(str, str, str, result=bool)
    def setLabel(self, field, text, token):
        self.calls.append(('setLabel', field, text, token))
        if not self.owner(token):
            return False
        self.data[field] = text
        self.publish()
        return True

    @Slot(str, result=bool)
    def finishLabels(self, token):
        self.calls.append(('finishLabels', token))
        return self.owner(token)

    @Slot(str, str, str, result=bool)
    def setTemplate(self, field, text, token):
        self.calls.append(('setTemplate', field, text, token))
        if not self.owner(token):
            return False
        self.data[field] = text
        self.publish()
        return True

    @Slot(str, str, result=bool)
    def command(self, action, token):
        self.calls.append(('command', action, token))
        return self.owner(token)

    @Slot(str, bool, str, result=bool)
    def setDisclosure(self, section, expanded, token):
        self.calls.append(('setDisclosure', section, expanded, token))
        if not self.owner(token):
            return False
        self.data['disclosures'] = {**self.data['disclosures'], section: expanded}
        self.publish()
        return True

    def activate(self, token):
        if token not in self.owners:
            self.owners[token] = deepcopy(self.data)
            self.owners[token].update(xmin='10.0000', plot_title='Incoming', title_pattern='incoming', disclosures={})
        self.token, self.data = token, self.owners[token]
        self.publish()


class SharedDatPropertiesQmlTests(unittest.TestCase):
    def setUp(self):
        self.messages = []
        self.previous_handler = qInstallMessageHandler(lambda kind, context, text: self.messages.append(text))
        self.bridge = PropertiesFixture()
        self.host = QuickPanel('SharedDatProperties.qml', self.bridge, bridge_property='bridge')
        self.host.resize(304, self.host.height())
        self.host.show()
        self.host.activateWindow()
        pump(70)

    def tearDown(self):
        self.host.close()
        self.host.deleteLater()
        APP.processEvents()
        qInstallMessageHandler(self.previous_handler)
        self.assertEqual([text for text in self.messages if 'SharedDatProperties.qml' in text], [])

    def edit(self, field, text, section='ranges', commit=False):
        self.bridge.focusRequested.emit(section, field)
        pump(50)
        control = item(self.host, field)
        self.assertTrue(control.hasActiveFocus(), field)
        QTest.keyClick(self.host, Qt.Key_A, Qt.ControlModifier)
        if text:
            QTest.keyClicks(self.host, text)
        else:
            QTest.keyClick(self.host, Qt.Key_Backspace)
        if commit:
            QTest.keyClick(self.host, Qt.Key_Return)
        pump(35)
        return control

    def test_load_dense_narrow_host_and_real_numeric_commit_and_step(self):
        self.assertEqual(self.host.status(), QQuickWidget.Ready)
        self.assertEqual(self.host.errors(), [])
        self.assertFalse(item(self.host, 'axis_override').isVisible())
        self.assertFalse(item(self.host, 'title_pattern').isVisible())
        field = self.edit('xmin', '.1234567', commit=True)
        self.assertEqual(field.property('text'), '0.1235')
        commits = [call for call in self.bridge.calls if call[:2] == ('commitNumber', 'xmin')]
        self.assertEqual(len(commits), 1)
        QTest.keyClick(self.host, Qt.Key_Up)
        pump()
        self.assertEqual(field.property('text'), '0.1335')
        self.assertIn(('stepNumber', 'xmin', 1, self.bridge.token), self.bridge.calls)
        self.assertLessEqual(field.width(), 140)

    def test_invalid_and_valid_drafts_survive_pointer_and_keyboard_disclosure(self):
        for draft in ('-', '0.56789'):
            with self.subTest(draft=draft):
                self.edit('xmin', draft)
                before = len([call for call in self.bridge.calls if call[0] == 'commitNumber'])
                click_item(self.host, 'shared_ranges_toggle')
                self.assertFalse(item(self.host, 'xmin').isVisible())
                self.assertEqual(self.bridge.data['xmin'], draft)
                header = item(self.host, 'shared_ranges_toggle')
                self.assertTrue(header.hasActiveFocus())
                QTest.keyClick(self.host, Qt.Key_Right)
                pump()
                self.assertTrue(item(self.host, 'xmin').isVisible())
                self.assertEqual(item(self.host, 'xmin').property('text'), draft)
                self.assertEqual(len([call for call in self.bridge.calls if call[0] == 'commitNumber']), before)

    def test_invalid_commit_reveals_field_and_error_without_losing_bytes(self):
        field = self.edit('vmin', '-', 'colors', commit=True)
        self.assertEqual(self.bridge.error_field, 'vmin')
        self.assertEqual(field.property('text'), '-')
        self.assertTrue(field.hasActiveFocus())
        self.assertTrue(self.bridge.data['disclosures']['colors'])

    def test_owner_switch_retires_focus_without_committing_or_clobbering_drafts(self):
        self.edit('xmin', '-')
        self.bridge.activate('dataset-b:graph-b')
        pump(50)
        self.assertFalse(item(self.host, 'xmin').hasActiveFocus())
        self.assertEqual(item(self.host, 'xmin').property('text'), '10.0000')
        self.assertEqual(self.bridge.owners['dataset-a:graph-a']['xmin'], '-')
        self.assertEqual([call for call in self.bridge.calls if call[0] == 'commitNumber'], [])
        self.edit('xmin', '11.25', commit=True)
        self.bridge.activate('dataset-a:graph-a')
        pump()
        self.assertEqual(item(self.host, 'xmin').property('text'), '-')
        self.assertEqual(self.bridge.owners['dataset-b:graph-b']['xmin'], '11.2500')

    def test_label_keystrokes_are_cheap_and_literal_and_finish_once(self):
        field = self.edit('plot_title', '<b>{literal}</b>', 'labels')
        self.assertEqual(self.bridge.data['plot_title'], '<b>{literal}</b>')
        self.assertEqual(field.property('text'), '<b>{literal}</b>')
        self.assertEqual([call for call in self.bridge.calls if call[0] in ('command', 'finishLabels')], [])
        QTest.keyClick(self.host, Qt.Key_Return)
        pump()
        self.assertEqual([call for call in self.bridge.calls if call[0] == 'finishLabels'], [('finishLabels', self.bridge.token)])
        self.edit('plot_title', '', 'labels')
        self.assertEqual(self.bridge.data['plot_title'], '')

    def test_templates_reveal_focus_and_preserve_invalid_text_across_collapse_owner(self):
        field = self.edit('title_pattern', '{broken', 'labels')
        self.assertTrue(field.isVisible())
        click_item(self.host, 'shared_templates_toggle')
        self.assertEqual(self.bridge.data['title_pattern'], '{broken')
        self.bridge.activate('dataset-b:graph-b')
        pump()
        self.assertFalse(item(self.host, 'title_pattern').isVisible())
        self.bridge.activate('dataset-a:graph-a')
        self.bridge.focusRequested.emit('labels', 'title_pattern')
        pump(50)
        self.assertEqual(field.property('text'), '{broken')
        self.assertTrue(field.hasActiveFocus())

    def test_regional_endpoints_disabled_with_independent_fix_and_source_availability(self):
        self.bridge.setChoice('color_scale', '3 regions', self.bridge.token)
        self.bridge.data['source_axis_enabled'] = False
        self.bridge.publish()
        pump()
        self.assertFalse(item(self.host, 'vmin').isEnabled())
        self.assertTrue(item(self.host, 'vmin_lock').isEnabled())
        self.assertTrue(item(self.host, 'edge0').isVisible())
        self.assertTrue(item(self.host, 'edge1').isVisible())
        click_item(self.host, 'vmin_lock')
        self.assertTrue(self.bridge.data['vmin_lock'])
        self.assertFalse(self.bridge.data['vmax_lock'])
        self.bridge.focusRequested.emit('ranges', 'axis_override')
        pump(50)
        self.assertTrue(item(self.host, 'axis_override').isVisible())
        self.assertFalse(item(self.host, 'axis_override').isEnabled())
        self.assertTrue(item(self.host, 'plot_title').isEnabled())
        self.assertTrue(item(self.host, 'xmin').isEnabled())

    def test_combo_popup_uses_own_window_and_fits_beyond_short_host(self):
        self.host.close()
        self.host.deleteLater()
        APP.processEvents()
        self.host = QuickPanel('SharedDatProperties.qml', self.bridge, expanding=True,
                               bridge_property='bridge')
        self.host.resize(304, 140)
        self.host.show()
        self.host.activateWindow()
        pump()
        click_item(self.host, 'colormap')
        combo = item(self.host, 'colormap')
        popup = next(child for child in combo.findChildren(QObject) if child.inherits('QQuickPopup'))
        view = popup.property('contentItem')
        rows = [child for child in view.childItems()[0].childItems() if child.inherits('QQuickItemDelegate')]
        self.assertEqual(len(rows), 10)
        window = rows[0].window()
        self.assertIsNot(window, self.host.quickWindow())
        self.assertGreater(window.height(), self.host.height())
        for row in rows:
            bottom = row.mapToScene(QPointF(row.width(), row.height()))
            self.assertLessEqual(bottom.y(), window.height() + 1)
        selected = next(row for row in rows if row.property('index') == 8)
        point = selected.mapToScene(QPointF(selected.width() / 2, selected.height() / 2))
        QTest.mouseClick(window, Qt.LeftButton, pos=point.toPoint())
        pump()
        self.assertEqual(self.bridge.data['colormap'], 'cividis')
        self.assertFalse(popup.property('visible'))

    def test_open_choice_is_retired_when_owner_changes(self):
        click_item(self.host, 'colormap')
        combo = item(self.host, 'colormap')
        popup = next(child for child in combo.findChildren(QObject) if child.inherits('QQuickPopup'))
        self.assertTrue(popup.property('visible'))
        self.bridge.activate('dataset-b:graph-b')
        pump()
        self.assertFalse(popup.property('visible'))
        self.assertEqual(self.bridge.data['colormap'], 'RdBu_r')
        self.assertEqual([call for call in self.bridge.calls if call[0] == 'setChoice'], [])

    def test_insert_template_field_uses_cursor_and_preserves_literal_format(self):
        self.bridge.focusRequested.emit('labels', 'title_pattern')
        pump(50)
        field = item(self.host, 'title_pattern')
        field.setProperty('cursorPosition', 2)
        click_item(self.host, 'insert_title_field')
        combo = item(self.host, 'insert_title_field')
        popup = next(child for child in combo.findChildren(QObject) if child.inherits('QQuickPopup'))
        view = popup.property('contentItem')
        rows = [child for child in view.childItems()[0].childItems() if child.inherits('QQuickItemDelegate')]
        selected = next(row for row in rows if row.property('index') == 3)
        point = selected.mapToScene(QPointF(selected.width() / 2, selected.height() / 2))
        QTest.mouseClick(selected.window(), Qt.LeftButton, pos=point.toPoint())
        pump()
        self.assertEqual(self.bridge.data['title_pattern'], 'E={valley}{voltage}')
        self.assertTrue(field.hasActiveFocus())
        self.assertEqual(field.property('cursorPosition'), 10)


if __name__ == '__main__':
    unittest.main()
