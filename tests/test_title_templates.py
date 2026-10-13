import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import json
import shutil
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch
from PIL import Image
from PySide6.QtWidgets import QApplication
from spectra_app.main_window import MainWindow
from spectra_app.image_export import _build_heatmap_export_figure
from tests.settings_helpers import isolate_settings

_APP = QApplication.instance() or QApplication([])
NAME = '$YZD320$~$pa$~$8T3.6KRef0.2sx10$~$680nmc$~$R1_85deg$~$0.95TG-BG=18$_rep01_001_avg13_DR_R_External_d2E_Regrid.dat'

class TitleParsingTests(unittest.TestCase):
    def test_builtin_extracts_voltage_rhs_and_scoped_valley(self):
        from spectra_app.title_templates import render_title, BUILTIN_PRESET
        self.assertEqual(render_title(NAME, BUILTIN_PRESET), 'E=18V 8T K')
        self.assertEqual(render_title(NAME.replace('85deg', '175deg').replace('=18$', '=18V$'), BUILTIN_PRESET), 'E=18V 8T Kp')
        self.assertEqual(render_title(NAME.replace('8T', '-8.5T').replace('=18$', '=-2.25$'), BUILTIN_PRESET), 'E=-2.25V -8.5T K')

    def test_custom_fields_literal_braces_and_angle_mapping(self):
        from spectra_app.title_templates import render_title
        preset = dict(name='Other', pattern='{{{sample}}} {temperature}K {angle}deg {valley}', angle_map={'85': 'OtherK'})
        self.assertEqual(render_title(NAME, preset), '{YZD320} 3.6K 85deg OtherK')

    def test_missing_unknown_and_ambiguous_fields_do_not_guess(self):
        from spectra_app.title_templates import render_title, BUILTIN_PRESET
        for name in ('unknown.dat', NAME.replace('85deg', '95deg'), NAME.replace('8T', '0.95TG'), NAME.replace('8T', '8T_9T_')):
            with self.subTest(name=name), self.assertRaises(ValueError):
                render_title(name, BUILTIN_PRESET)

    def test_unsafe_unknown_or_malformed_template_is_rejected(self):
        from spectra_app.title_templates import validate_preset
        for pattern in ('{voltage.real}', '{voltage[0]}', '{voltage!r}', '{voltage:>9}', '{unknown}', '{}', '{'):
            with self.subTest(pattern=pattern), self.assertRaises(ValueError):
                validate_preset(dict(name='Bad', pattern=pattern, angle_map={}))

    def test_omitted_valley_does_not_require_angle_mapping(self):
        from spectra_app.title_templates import render_title
        preset = dict(name='No valley', pattern='{field}T E={voltage}V', angle_map={})
        self.assertEqual(render_title(NAME.replace('85deg', '95deg'), preset), '8T E=18V')

    def test_duplicate_angle_keys_are_rejected(self):
        from spectra_app.title_templates import parse_angle_map
        with self.assertRaises(ValueError):
            parse_angle_map('85=K;85.0=Other')

class TitleWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory = isolate_settings(self).parent
        self.windows = []
    def tearDown(self):
        for window in self.windows:
            window.close()
    def window(self):
        window = MainWindow()
        self.windows.append(window)
        return window
    def dat(self, name=NAME):
        path = self.directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('Energy(eV) 0 1 2\n' + '\n'.join(f'{1+i/10} {i+1} {i+2} {i+3}' for i in range(12)), encoding='utf-8')
        return path
    def select_builtin(self, w):
        w.title_preset_combo.setCurrentIndex(w.title_preset_combo.findText('Current group'))
    def test_two_new_files_update_title_with_fixed_yz_and_missing_clears(self):
        w = self.window()
        self.select_builtin(w)
        w.y_axis_name_edit.setText('Bias')
        w.y_axis_unit_edit.setText('V')
        w._load_dat_file_direct(self.dat())
        self.assertEqual(w._heat_ax.get_title(), 'E=18V 8T K')
        w._load_dat_file_direct(self.dat(NAME.replace('85deg', '175deg').replace('=18$', '=20$')))
        self.assertEqual(w._heat_ax.get_title(), 'E=20V 8T Kp')
        self.assertEqual(w._heat_ax.get_ylabel(), 'Bias (V)')
        w._load_dat_file_direct(self.dat('unknown.dat'))
        self.assertEqual(w._heat_ax.get_title(), '')
        self.assertIn('Missing', w.title_template_status.text())
    def test_override_blank_axis_reload_and_apply_template(self):
        w = self.window()
        self.select_builtin(w)
        p = self.dat()
        w._load_dat_file_direct(p)
        for title in ('My $literal$ title', ''):
            w.plot_title_edit.setText(title)
            w.x_axis_override_combo.setCurrentIndex(w.x_axis_override_combo.findData('pixel'))
            w._on_x_axis_override_changed()
            self.assertEqual(w._heat_ax.get_title(), title)
            w._load_dat_file_direct(p)
            self.assertEqual(w._heat_ax.get_title(), title)
            self.assertEqual(w._title_settings['manual_override'], title)
        w.title_apply_button.click()
        self.assertEqual(w._heat_ax.get_title(), 'E=18V 8T K')
    def test_custom_preset_restart_and_portable_snapshot(self):
        w = self.window()
        self.select_builtin(w)
        w.title_pattern_edit.setText('{valley}: {field}T / E={voltage}V')
        w.title_angle_map_edit.setText('85=OtherK;175=OtherKp')
        w.title_preset_name_edit.setText('My group')
        w.title_save_button.click()
        p = self.dat()
        w._load_dat_file_direct(p)
        self.assertEqual(w._heat_ax.get_title(), 'OtherK: 8T / E=18V')
        w.close()
        restored = self.window()
        self.assertEqual(restored.title_preset_combo.currentText(), 'My group')
        restored._load_dat_file_direct(self.dat(NAME.replace('85deg', '175deg')))
        self.assertEqual(restored._heat_ax.get_title(), 'OtherKp: 8T / E=18V')
        copied = self.directory / 'copy' / p.name
        copied.parent.mkdir()
        shutil.copy2(p, copied)
        shutil.copy2(str(p)+'.plot.json', str(copied)+'.plot.json')
        restored._annotation_settings.clear()
        portable = self.window()
        portable._load_dat_file_direct(copied)
        self.assertEqual(portable._heat_ax.get_title(), 'OtherK: 8T / E=18V')
        portable.title_apply_button.click()
        self.assertEqual(portable._heat_ax.get_title(), 'OtherK: 8T / E=18V')
        portable._load_dat_file_direct(self.dat('new.dat'))
        self.assertEqual(portable.title_preset_combo.currentText(), 'Manual')
    def test_v1_migration_preserves_all_plot_settings_and_blank_title(self):
        w = self.window()
        p = self.dat()
        w._load_dat_file_direct(p)
        w.plot_title_edit.clear()
        w.y_axis_name_edit.setText('Legacy bias')
        w.cmap_combo.setCurrentText('viridis')
        w.close()
        sidecar = w._plot_state_store.path_for(p)
        payload = json.loads(sidecar.read_text(encoding='utf-8'))
        payload['version'] = 1
        payload['plot'].pop('title_settings', None)
        # v1 predates both title templates and saved processing recipes.
        payload['plot'].pop('processing')
        # Formal presentation and color mapping were added in v3.
        for field in ('norm', 'presentation', 'color_region_limits'):
            payload['plot'].pop(field)
        original = json.loads(json.dumps(payload['plot']))
        sidecar.write_text(json.dumps(payload), encoding='utf-8')
        from spectra_app.plot_state import source_fingerprint
        loaded, notice = w._plot_state_store.load(p, source_fingerprint(p))
        self.assertEqual(notice, '')
        migrated_fields = ('title_settings', 'processing', 'norm', 'presentation', 'color_region_limits')
        self.assertEqual({k:v for k,v in loaded.items() if k not in migrated_fields}, original)
        self.assertEqual(loaded['title_settings']['manual_override'], '')
        self.assertEqual(loaded['processing'], {
            'mode': 'original', 'smooth': True, 'window_length': 21,
            'original_signal_title': 'ΔR/R₀ (arb. units)',
        })
        self.assertEqual(loaded['norm'], {'kind': 'linear', 'linthresh': 1.0})
        self.assertEqual(loaded['presentation']['width_in'], 3.3)
        self.assertEqual(loaded['color_region_limits'], [])
        restored = self.window()
        self.select_builtin(restored)
        restored._load_dat_file_direct(p)
        self.assertEqual(restored._heat_ax.get_title(), '')
        self.assertEqual(restored.cmap_combo.currentText(), 'viridis')
        self.assertEqual(restored.title_preset_combo.currentText(), 'Manual')
    def test_export_keeps_generated_literal_title_and_saves_v3(self):
        w = self.window()
        self.select_builtin(w)
        p = self.dat()
        w._load_dat_file_direct(p)
        fig = _build_heatmap_export_figure(w.figure, selected_axes=(w._heat_ax, w._heat_colorbar.ax))
        self.assertIn('E=18V 8T K', [t.get_text() for t in fig.texts])
        w._render_plot_page()
        page = w.plot_page.rendered_page
        self.assertIsNotNone(page, w.plot_page.status_label.text())
        self.assertIn('E=18V 8T K', ''.join(ET.fromstring(page.svg_bytes).itertext()))
        with patch.object(w, '_show_error', side_effect=AssertionError('PNG export must succeed')):
            w._on_export_png()
        output = p.with_name(p.stem + '_export01.png')
        self.assertTrue(output.is_file())
        with Image.open(output) as image:
            metadata = json.loads(image.info['SpectraAnalyzer'])
        self.assertEqual(metadata['plot_title'], 'E=18V 8T K')
        self.assertEqual(metadata['source_path'], str(p))
        payload = json.loads(w._plot_state_store.path_for(p).read_text(encoding='utf-8'))
        self.assertEqual(payload['version'], 3)
        self.assertEqual(payload['plot']['annotations']['plot_title'], 'E=18V 8T K')

    def test_template_override_never_pins_future_title_or_global_manual_default(self):
        w = self.window()
        w.plot_title_edit.setText('Legacy manual default')
        self.select_builtin(w)
        first = self.dat()
        w._load_dat_file_direct(first)
        w.plot_title_edit.setText('Only this DAT')
        w.fix_annotations_chk.setChecked(False)
        w.fix_annotations_chk.setChecked(True)
        second = self.dat(NAME.replace('85deg', '175deg'))
        w._load_dat_file_direct(second)
        self.assertEqual(w._heat_ax.get_title(), 'E=18V 8T Kp')
        self.assertEqual(w._annotation_settings.value('plot_annotations/plot_title'), 'Legacy manual default')
        w._load_dat_file_direct(first)
        self.assertEqual(w._heat_ax.get_title(), 'Only this DAT')
        w.close()
        restarted = self.window()
        restarted._load_dat_file_direct(first)
        self.assertEqual(restarted._heat_ax.get_title(), 'Only this DAT')
        restarted._load_dat_file_direct(self.dat(NAME.replace('=18$', '=22$')))
        self.assertEqual(restarted._heat_ax.get_title(), 'E=22V 8T K')

    def test_blank_override_survives_portable_custom_snapshot(self):
        w = self.window()
        self.select_builtin(w)
        p = self.dat()
        w._load_dat_file_direct(p)
        w.plot_title_edit.clear()
        w.close()
        w._annotation_settings.clear()
        restarted = self.window()
        restarted._load_dat_file_direct(p)
        self.assertEqual(restarted._heat_ax.get_title(), '')
        self.assertEqual(restarted._title_settings['manual_override'], '')
        self.assertEqual(restarted.title_pattern_edit.text(), 'E={voltage}V {field}T {valley}')

    def test_invalid_v2_title_snapshot_is_rejected_before_partial_restore(self):
        w = self.window()
        self.select_builtin(w)
        p = self.dat()
        w._load_dat_file_direct(p)
        w.close()
        sidecar = w._plot_state_store.path_for(p)
        original = json.loads(sidecar.read_text(encoding='utf-8'))
        from spectra_app.plot_state import source_fingerprint
        for mutation in (
                lambda title: title.update(mode='other'),
                lambda title: title.update(manual_override=False),
                lambda title: title['preset'].update(pattern='{voltage.__class__}'),
                lambda title: title['preset'].update(angle_map={'85': 123}),
                lambda title: title.update(extra=True)):
            payload = json.loads(json.dumps(original))
            mutation(payload['plot']['title_settings'])
            sidecar.write_text(json.dumps(payload), encoding='utf-8')
            loaded, notice = w._plot_state_store.load(p, source_fingerprint(p))
            self.assertIsNone(loaded)
            self.assertIn('ignored', notice)

    def test_bad_editor_text_clears_title_without_saving_unsafe_template(self):
        w = self.window()
        self.select_builtin(w)
        w._load_dat_file_direct(self.dat())
        w.title_pattern_edit.setText('{voltage.__class__}')
        self.assertEqual(w._heat_ax.get_title(), '')
        self.assertIn('Use only', w.title_template_status.text())
        w.title_preset_name_edit.setText('Bad')
        w.title_save_button.click()
        self.assertNotIn('Bad', w._title_presets)

    def test_missing_field_explanation_survives_sidecar_restore(self):
        w = self.window()
        self.select_builtin(w)
        p = self.dat('unknown.dat')
        w._load_dat_file_direct(p)
        self.assertIn('Missing', w.title_template_status.text())
        w._load_dat_file_direct(p)
        self.assertEqual(w._heat_ax.get_title(), '')
        self.assertIn('Missing', w.title_template_status.text())
        self.assertEqual(w._title_settings['manual_override'], None)
