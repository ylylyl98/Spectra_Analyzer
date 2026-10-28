"""Widget-free presentation authority for advanced source navigation.

Scientific owners, imports and project transactions stay behind explicit services.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, Property, Signal, Slot

from .dataset_state import dataset_display_labels


@dataclass(frozen=True)
class SourceServices:
    workspace: Callable
    busy: Callable
    import_data: Callable
    activate_dataset: Callable
    activate_graph: Callable
    browse_root: Callable
    select_folder: Callable
    load_paths: Callable
    dataset_action: Callable
    graph_action: Callable
    save_rows: Callable
    error: Callable


class SourceNavigation(QObject):
    stateChanged = Signal()
    availabilityChanged = Signal()

    def __init__(self, services: SourceServices, parent=None, *, visible_rows=8):
        super().__init__(parent)
        self.services = services
        self._workspace = None
        self._epoch = 0
        self._query = ''
        self._rows = []
        self._graphs = []
        self._active_dataset = self._active_graph = ''
        self._context_text = 'No DAT source selected.'
        self._visible_rows = max(4, min(12, int(visible_rows)))
        self._status = 'No imported datasets. Import or drop DAT files to begin.'
        self._pending = False
        self._import_blocks = set()
        self._root = self._folder = self._file = ''
        self._folders = []
        self._files = []
        self._discovery = 0
        self.sync()

    def sync(self, *, reset_status=False):
        workspace = self.services.workspace()
        if workspace is not self._workspace:
            self._workspace = workspace
            self._epoch += 1
        sessions = list(workspace.datasets.values())
        labels = dataset_display_labels(sessions)
        self._rows = [{'datasetId': session.dataset_id,
                       'label': labels[session.dataset_id],
                       'path': str(session.source_path.resolve()) if session.source_path else '',
                       'searchName': session.source_path.name.casefold() if session.source_path else '',
                       'searchFolder': str(session.source_path.parent).casefold() if session.source_path else ''}
                      for session in sessions]
        self._active_dataset = workspace.active_dataset_id or ''
        active = workspace.active
        self._graphs = ([{'graphId': g.graph_id, 'name': g.name}
                         for g in active.graphs.values() if g.published] if active else [])
        self._active_graph = active.active_graph_id if active else ''
        self._context_text = 'No DAT source selected.'
        if active is not None:
            name = active.source_path.name if active.source_path else 'Embedded data'
            graph = active.active_graph
            self._context_text = (f'Graph: {graph.name}\n' if graph.published else '') + f'Source: {name}'
        if reset_status and not self._pending:
            self._status = (f'{len(sessions)} imported dataset(s).' if sessions else
                            'No imported datasets. Import or drop DAT files to begin.')
        self.stateChanged.emit()
        self.availabilityChanged.emit()

    def set_import_status(self, text):
        self._status = str(text)
        self.stateChanged.emit()

    def set_import_pending(self, pending):
        self._pending = bool(pending)
        if pending:
            self.set_import_status('Preparing data… Dataset switching and analysis are paused.')
        self.stateChanged.emit()
        self.availabilityChanged.emit()

    def set_import_available(self, available, *, reason='host'):
        """Independent host gates survive the temporary request lock."""
        if available:
            self._import_blocks.discard(reason)
        else:
            self._import_blocks.add(reason)
        self.availabilityChanged.emit()

    def reset_inventory(self, *, clear_search=True):
        self._discovery += 1
        self._root = self._folder = self._file = ''
        self._folders = []
        self._files = []
        if clear_search:
            self._query = ''
        self.stateChanged.emit()

    def begin_root(self, root):
        self.reset_inventory(clear_search=False)
        self._root = str(Path(root).resolve())
        self.stateChanged.emit()
        return self.inventory_ticket()

    def inventory_ticket(self):
        return (self.services.workspace(), self._epoch, self._discovery, self._root, self._folder)

    def inventory_current(self, ticket):
        return (ticket[0] is self.services.workspace() and ticket[1:] == self.inventory_ticket()[1:])

    def apply_folders(self, ticket, folders):
        if not self.inventory_current(ticket):
            return False
        root = Path(self._root)
        self._folders = [{'path': str(Path(p).resolve()),
                          'label': '.' if Path(p).resolve() == root else str(Path(p).resolve().relative_to(root))}
                         for p in folders]
        self.stateChanged.emit()
        if self._folders:
            self.selectFolder(self._folders[0]['path'])
        return True

    def begin_folder(self, folder):
        self._discovery += 1
        self._folder = str(Path(folder).resolve())
        self._file = ''
        self._files = []
        self.stateChanged.emit()
        return self.inventory_ticket()

    def apply_files(self, ticket, names):
        if not self.inventory_current(ticket):
            return False
        folder = Path(ticket[4])
        self._files = [{'name': Path(name).name, 'path': str((folder / name).resolve())} for name in names]
        self._file = self._files[0]['path'] if self._files else ''
        self.stateChanged.emit()
        return True

    def _run(self, callback, *args):
        try:
            callback(*args)
        except ValueError as exc:
            self.sync()
            self.services.error(str(exc))
            return False
        self.sync()
        return True

    @Property('QVariantList', notify=stateChanged)
    def datasetRows(self):
        query = self._query.strip().casefold()
        return [row for row in self._rows if not query or
                query in row['searchName'] or query in row['searchFolder']]

    @Property('QVariantList', notify=stateChanged)
    def graphRows(self): return self._graphs
    @Property('QVariantList', notify=stateChanged)
    def folderRows(self): return self._folders
    @Property('QVariantList', notify=stateChanged)
    def fileRows(self): return self._files
    @Property(str, notify=stateChanged)
    def searchText(self): return self._query
    @Property(str, notify=stateChanged)
    def activeDatasetId(self): return self._active_dataset
    @Property(str, notify=stateChanged)
    def activeGraphId(self): return self._active_graph
    @Property(str, notify=stateChanged)
    def selectedFolder(self): return self._folder
    @Property(str, notify=stateChanged)
    def selectedFile(self): return self._file
    @Property(str, notify=stateChanged)
    def rootPath(self): return self._root
    @Property(str, notify=stateChanged)
    def importStatus(self): return self._status
    @Property(str, notify=stateChanged)
    def sourceContext(self): return self._context_text
    @Property(int, notify=stateChanged)
    def visibleRows(self): return self._visible_rows
    @Property(bool, notify=stateChanged)
    def noMatches(self): return bool(self._rows and not self.datasetRows)
    @Property(str, notify=stateChanged)
    def countText(self):
        count = f'{len(self.datasetRows)} / {len(self._rows)} shown' if self._query.strip() else str(len(self._rows))
        return f'Imported datasets ({count})'
    @Property(bool, notify=stateChanged)
    def navigationEnabled(self): return not self._pending
    @Property(bool, notify=availabilityChanged)
    def importEnabled(self): return not self._pending and not self._import_blocks
    @Property(bool, notify=availabilityChanged)
    def graphsEnabled(self):
        active = self.services.workspace().active
        return bool(not self._pending and not self.services.busy() and active and active.current_cube is not None)
    @Property(bool, notify=stateChanged)
    def loadEnabled(self): return not self._pending and bool(self._file)

    @Slot(str)
    def setSearchText(self, text):
        self._query = text
        self.stateChanged.emit()

    @Slot(int)
    def setVisibleRows(self, rows):
        rows = max(4, min(12, rows))
        if rows != self._visible_rows:
            self._visible_rows = rows
            self.services.save_rows(rows)
            self.stateChanged.emit()

    @Slot(str, result=bool)
    def selectDataset(self, dataset_id):
        if self._pending or dataset_id not in self.services.workspace().datasets:
            return False
        if dataset_id == self.services.workspace().active_dataset_id:
            return True
        return self._run(self.services.activate_dataset, dataset_id)

    @Slot(int, result=bool)
    def moveDataset(self, delta):
        rows = self.datasetRows
        if not rows or self._pending:
            return False
        identities = [row['datasetId'] for row in rows]
        index = identities.index(self._active_dataset) if self._active_dataset in identities else (-1 if delta > 0 else len(rows))
        return self.selectDataset(identities[max(0, min(len(rows) - 1, index + delta))])

    @Slot(str, result=bool)
    def selectGraph(self, graph_id):
        if not self.graphsEnabled or graph_id not in [row['graphId'] for row in self._graphs]:
            return False
        return self._run(self.services.activate_graph, graph_id)

    @Slot()
    def importData(self):
        if self.importEnabled:
            self.services.import_data()

    @Slot()
    def browseRoot(self):
        if not self._pending:
            self.services.browse_root()

    @Slot(str, result=bool)
    def selectFolder(self, folder):
        if self._pending or folder not in [row['path'] for row in self._folders]:
            return False
        self.services.select_folder(folder)
        return True

    @Slot(str)
    def selectFile(self, path):
        if not self._pending and path in [row['path'] for row in self._files]:
            self._file = path
            self.stateChanged.emit()

    @Slot(int)
    def moveFile(self, delta):
        if self._pending or not self._files:
            return
        paths = [row['path'] for row in self._files]
        index = paths.index(self._file) if self._file in paths else (-1 if delta > 0 else len(paths))
        self.selectFile(paths[max(0, min(len(paths) - 1, index + delta))])

    @Slot()
    def loadSelected(self):
        if self.loadEnabled:
            self.services.load_paths([Path(self._file)])

    @Slot(str, result='QVariantMap')
    def datasetContext(self, dataset_id):
        self.sync()
        return {'epoch': self._epoch, 'datasetId': dataset_id}

    @Slot(result='QVariantMap')
    def graphContext(self):
        self.sync()
        result = {'epoch': self._epoch, 'datasetId': self._active_dataset}
        result['graphId'] = self._active_graph
        return result

    def _valid_context(self, context):
        workspace = self.services.workspace()
        return (workspace is self._workspace and context.get('epoch') == self._epoch and
                context.get('datasetId') in workspace.datasets)

    @Slot('QVariantMap', str, result=bool)
    def datasetActionEnabled(self, context, action):
        return (self._valid_context(context) and action in ('copy', 'source', 'export', 'remove') and
                (action != 'remove' or not self.services.busy()) and not self._pending)

    @Slot('QVariantMap', str, result=bool)
    def invokeDatasetAction(self, context, action):
        if not self.datasetActionEnabled(context, action):
            return False
        return self._run(self.services.dataset_action, action, context['datasetId'])

    @Slot('QVariantMap', str, result=bool)
    def graphActionEnabled(self, context, action):
        if not self._valid_context(context) or not self.graphsEnabled:
            return False
        active = self.services.workspace().active
        if (active.dataset_id != context.get('datasetId') or active.active_graph_id != context.get('graphId')):
            return False
        return (action == 'new' or active.active_graph.published and
                (action == 'rename' or action == 'remove' and len(active.graphs) > 1))

    @Slot('QVariantMap', str, result=bool)
    def invokeGraphAction(self, context, action):
        if not self.graphActionEnabled(context, action):
            return False
        return self._run(self.services.graph_action, action, context['datasetId'], context['graphId'])
