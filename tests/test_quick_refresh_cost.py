"""Refresh cost and authorization contracts through the production Quick root."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


class QuickRefreshCostTests(unittest.TestCase):
    def test_actual_quick_refresh(self):
        environment = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software')
        # Windows' offscreen font loader otherwise searches a fonts directory
        # omitted by the PySide wheel. Use installed system fonts, without
        # suppressing Qt diagnostics or changing production font selection.
        system_fonts = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts'
        if sys.platform == 'win32' and system_fonts.is_dir():
            environment.setdefault('QT_QPA_FONTDIR', str(system_fonts))
        result = subprocess.run(
            [sys.executable, '-X', 'utf8', str(Path(__file__).resolve()), '--probe'],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
            env=environment,
            timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, '', result.stderr)
        self.assertIn('Refresh contracts: PASS after Quick teardown', result.stdout)


def probe():
    from unittest.mock import patch
    from PySide6.QtTest import QSignalSpy
    from tests.quick_application_helpers import application, item, pump

    metrics = {}
    with application() as (host, folder, errors):
        w = host.controller
        source = folder / 'refresh.dat'
        source.write_text('Energy(eV) 0 1 2\n' + '\n'.join(
            f'{1+i/10} {i+1} {2*i+2} {3*i+3}' for i in range(13)), encoding='utf8')
        w._load_dat_file_direct(source)
        page = w.open_graph_document()
        w._render_plot_page()
        w.request_task(2)
        pump(100)
        key, bridge, documents = w._document_key(), page.formal, w.graph_documents
        assert page.rendered_page is not None
        assert documents.content_item(key) is not None
        editor = item(host, 'width_in')
        assert editor.isEnabled()

        # Repeated caller refreshes must not wake every formal QML binding.
        bridge.sync()
        formal_signals = QSignalSpy(bridge.stateChanged)
        document_signals = QSignalSpy(documents.stateChanged)
        model_signals = QSignalSpy(documents.frames.dataChanged)
        for _ in range(8):
            w._refresh_graph_commands()
        metrics['eight_unchanged_refreshes'] = {
            'formal_notifications': formal_signals.count(),
            'document_notifications': document_signals.count(),
            'model_notifications': model_signals.count()}
        assert metrics['eight_unchanged_refreshes'] == {
            'formal_notifications': 0, 'document_notifications': 0, 'model_notifications': 0}
        with patch.object(bridge, 'allowed', wraps=bridge.allowed) as allowed:
            state = bridge.state
            assert allowed.call_count == 1
        metrics['command_guard_evaluations_per_state'] = allowed.call_count
        assert state['commands']['analyze']['enabled']

        # Real title changes still reach both frame and tab delegates immediately.
        title = documents._presentations[key].title
        documents.update_title(key, title + ' renamed')
        assert document_signals.count() == 1 and model_signals.count() == 1
        frame = item(host, 'documentFrame_' + '_'.join(key))
        assert frame.property('documentTitle') == title + ' renamed'
        tab = item(host, 'documentTab_' + '_'.join(key))
        assert tab.property('text') == title + ' renamed'
        documents.update_title(key, title + ' renamed')
        assert document_signals.count() == 1 and model_signals.count() == 1
        documents.update_title(key, title)

        # Equal raw flags must still publish guard changes and disable QML input.
        baseline = formal_signals.count()
        actions = QSignalSpy(page.analysisRequested)
        with patch.object(w, '_project_busy', return_value=True):
            page.set_command_availability('analyze', True)
            assert formal_signals.count() == baseline + 1
            assert not bridge.enabled and not editor.isEnabled()
            assert not bridge.state['commands']['analyze']['enabled']
            bridge.command('analyze')
            assert actions.count() == 0
            # Cancel remains callable while a project operation owns input.
            cancelled = QSignalSpy(page.batchCancelRequested)
            page.set_command_availability('batch_cancel', True)
            assert bridge.state['commands']['batch_cancel']['enabled']
            bridge.command('batch_cancel')
            assert cancelled.count() == 1
        page.set_command_availability('analyze', True)
        assert bridge.enabled and editor.isEnabled()
        assert bridge.state['commands']['analyze']['enabled']

        # enabled is separately notified even when every command is disabled.
        for command in bridge._commands:
            page.set_command_availability(command, False)
        baseline = formal_signals.count()
        with patch.object(w, '_project_busy', return_value=True):
            page.set_command_availability('analyze', False)
            assert formal_signals.count() == baseline + 1
            assert not editor.isEnabled()
        page.set_command_availability('analyze', False)
        assert formal_signals.count() == baseline + 2
        assert editor.isEnabled()

        # A nested publication owns the latest signature when the outer returns.
        nested = []
        def update_during_signal():
            if not nested:
                nested.append(True)
                page.set_command_availability('analyze', False, 'nested')
        bridge.stateChanged.connect(update_during_signal)
        page.set_command_availability('analyze', True, 'outer')
        bridge.stateChanged.disconnect(update_during_signal)
        baseline = formal_signals.count()
        page.set_command_availability('analyze', False, 'nested')
        assert formal_signals.count() == baseline

        # The optimization never caches action authorization or mutable drafts.
        page.set_command_availability('analyze', True)
        owner = page.expected_owner
        page.expected_owner = ('stale-owner',)
        page.set_command_availability('analyze', True)
        assert not bridge.enabled and not editor.isEnabled()
        bridge.command('analyze')
        assert actions.count() == 0
        page.expected_owner = owner
        page.set_command_availability('analyze', True)
        assert editor.isEnabled()
        with patch.object(bridge, '_input_guard', return_value=False):
            bridge.command('analyze')
            bridge.setDraft('width_in', '-')
        assert actions.count() == 0 and not bridge.has_drafts()
        bridge.setDraft('width_in', '-')
        assert not bridge.commitField('width_in')
        before = (dict(bridge._drafts), page.presentation_error, bridge._error_field)
        for _ in range(8):
            page.set_command_availability('analyze', True)
            documents.update_title(key, title)
        assert (bridge._drafts, page.presentation_error, bridge._error_field) == before
        assert bridge.state['fields']['width_in'] == '-' and editor.property('text') == '-'
        bridge.setDraft('width_in', str(page.presentation.width_in))
        assert bridge.commitField('width_in')

        page.dispose()
        page.set_command_availability('analyze', True)
        page.set_command_availability('batch_cancel', True)
        bridge.command('analyze')
        bridge.command('batch_cancel')
        assert actions.count() == 0 and cancelled.count() == 1
        assert not bridge.enabled
        assert not any(value['enabled'] for value in bridge.state['commands'].values())
        assert not errors, errors
    print(json.dumps(metrics, indent=2), flush=True)
    print('Refresh contracts: PASS after Quick teardown', flush=True)


if __name__ == '__main__':
    if '--probe' in sys.argv:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        probe()
    else:
        unittest.main()
