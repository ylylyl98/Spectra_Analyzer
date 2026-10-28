"""Explicit operation-port and stale identity checks for source navigation."""
from pathlib import Path
import unittest

from spectra_app.dataset_state import DatasetSession, Workspace
from spectra_app.source_navigation import SourceNavigation, SourceServices


class SourceOperations:
    """Service fake with domain owners; deliberately has no widget/window API."""
    def __init__(self):
        self.workspace = Workspace()
        for identity in ('first', 'second', 'third'):
            session = DatasetSession(dataset_id=identity, source_path=Path(identity, 'sample.dat').resolve())
            session.current_cube = object()
            session.active_graph.published = True
            self.workspace.register(session)
        self.workspace.active_dataset_id = 'first'
        self.calls = []
        self.busy = False
        self.controller = SourceNavigation(SourceServices(
            workspace=lambda: self.workspace, busy=lambda: self.busy,
            import_data=lambda: self.calls.append(('import',)),
            activate_dataset=self.activate_dataset, activate_graph=self.activate_graph,
            browse_root=lambda: self.calls.append(('browse',)), select_folder=self.select_folder,
            load_paths=lambda paths: self.calls.append(('load', paths)),
            dataset_action=lambda action, identity: self.calls.append((action, identity)),
            graph_action=lambda action, dataset, graph: self.calls.append((action, dataset, graph)),
            save_rows=lambda rows: self.calls.append(('rows', rows)),
            error=lambda text: self.calls.append(('error', text))))

    def activate_dataset(self, identity):
        self.calls.append(('activate', identity))
        self.workspace.active_dataset_id = identity

    def activate_graph(self, identity):
        self.calls.append(('graph', identity))
        self.workspace.active.activate_graph(identity)

    def select_folder(self, folder):
        self.calls.append(('folder', folder))
        self.controller.begin_folder(folder)


class SourceNavigationControllerTests(unittest.TestCase):
    def test_query_owner_notice_and_rows_are_independent(self):
        operations = SourceOperations()
        controller = operations.controller
        controller.set_import_status('Partial failure')
        controller.setSearchText('  SECOND  ')
        self.assertEqual([row['datasetId'] for row in controller.datasetRows], ['second'])
        self.assertEqual(controller.activeDatasetId, 'first')
        self.assertEqual(controller.importStatus, 'Partial failure')
        self.assertEqual(operations.calls, [])
        self.assertTrue(controller.moveDataset(1))
        self.assertEqual(controller.activeDatasetId, 'second')
        controller.setSearchText('missing')
        self.assertFalse(controller.moveDataset(-1))
        self.assertTrue(controller.noMatches)
        self.assertEqual(controller.activeDatasetId, 'second')
        controller.setVisibleRows(99)
        self.assertEqual(controller.visibleRows, 12)
        self.assertEqual(operations.calls[-1], ('rows', 12))

    def test_captured_context_rejects_workspace_replacement_busy_and_graph_owner_changes(self):
        operations = SourceOperations()
        controller = operations.controller
        context = controller.datasetContext('second')
        self.assertTrue(controller.invokeDatasetAction(context, 'copy'))
        self.assertEqual(operations.calls[-1], ('copy', 'second'))
        self.assertEqual(controller.activeDatasetId, 'first')
        operations.busy = True
        self.assertFalse(controller.invokeDatasetAction(context, 'remove'))
        operations.busy = False
        graph = controller.graphContext()
        self.assertTrue(controller.invokeGraphAction(graph, 'rename'))
        operations.workspace.active.create_graph('Other')
        self.assertFalse(controller.invokeGraphAction(graph, 'rename'))
        operations.workspace = SourceOperations().workspace
        notifications = []
        controller.stateChanged.connect(lambda: notifications.append('state'))
        controller.availabilityChanged.connect(lambda: notifications.append('availability'))
        self.assertFalse(controller.datasetActionEnabled(context, 'copy'))
        self.assertFalse(controller.graphActionEnabled(graph, 'rename'))
        self.assertFalse(controller.invokeDatasetAction(context, 'copy'))
        self.assertFalse(controller.invokeGraphAction(graph, 'rename'))
        self.assertEqual(notifications, [], 'Permission checks must not synchronize a replacement workspace')
        fresh_context = controller.datasetContext('second')
        fresh_graph = controller.graphContext()
        notifications.clear()
        self.assertFalse(controller.datasetActionEnabled(context, 'copy'))
        self.assertFalse(controller.graphActionEnabled(graph, 'rename'))
        self.assertTrue(controller.datasetActionEnabled(fresh_context, 'copy'))
        self.assertTrue(controller.graphActionEnabled(fresh_graph, 'rename'))
        self.assertEqual(notifications, [])
        self.assertTrue(controller.invokeDatasetAction(fresh_context, 'copy'))
        self.assertEqual(operations.calls[-1], ('copy', 'second'))

    def test_inventory_absolute_paths_and_publication_ticket(self):
        operations = SourceOperations()
        controller = operations.controller
        root = Path('inventory').resolve()
        ticket = controller.begin_root(root)
        self.assertTrue(controller.apply_folders(ticket, [root / 'first']))
        files = controller.inventory_ticket()
        self.assertTrue(controller.apply_files(files, ['sample.dat']))
        controller.loadSelected()
        self.assertEqual(operations.calls[-1], ('load', [root / 'first' / 'sample.dat']))
        controller.begin_folder(root / 'second')
        self.assertFalse(controller.apply_files(files, ['late.dat']))
        files = controller.inventory_ticket()
        operations.workspace = SourceOperations().workspace
        self.assertFalse(controller.apply_files(files, ['late.dat']))

    def test_cancel_unlock_keeps_independent_import_gate(self):
        operations = SourceOperations()
        controller = operations.controller
        controller.set_import_available(False, reason='closing')
        controller.set_import_pending(True)
        controller.importData()
        self.assertFalse(controller.selectDataset('second'))
        controller.set_import_pending(False)
        self.assertTrue(controller.navigationEnabled)
        self.assertFalse(controller.importEnabled)
        controller.importData()
        self.assertEqual(operations.calls, [])
        controller.set_import_available(True, reason='closing')
        controller.importData()
        self.assertEqual(operations.calls, [('import',)])


if __name__ == '__main__':
    unittest.main()
