"""Qt Quick import review, detached from the workspace until accepted."""
from __future__ import annotations

from PySide6.QtCore import Slot

from .quick_dialog import QuickDialog
from .xy_import_bridge import XYImportBridge, _PreviewModel, validate_import_mapping


class XYImportDialog(QuickDialog):
    def __init__(self, tables, workbooks, parent=None, *, workbook_id=None, mappings=None, reused=None,
                 comparisons=None, comparison_id=None, entries=None):
        bridge = XYImportBridge(tables, workbooks, mappings=mappings, reused=reused,
            comparisons=comparisons, comparison_id=comparison_id, entries=entries)
        super().__init__(bridge, 'XYImportDialog.qml', parent,
            title='Import data' if entries is not None else 'Import numeric XY files', size=(920, 620))
        bridge.commitRequested.connect(self._commit)
        self.compare = False

    @property
    def tables(self):
        return self.bridge.tables

    @property
    def entries(self):
        return self.bridge.entries

    @property
    def mappings(self):
        return self.bridge.mappings

    @mappings.setter
    def mappings(self, value):
        self.bridge.replaceMappings(value)

    @property
    def reused(self):
        return self.bridge.reused

    @property
    def destination(self):
        return self.bridge.destination

    @property
    def workbook_name(self):
        return self.bridge.workbookName

    @property
    def comparison_id(self):
        return self.bridge.comparison_id

    @property
    def include_existing(self):
        return self.bridge.includeExisting

    def set_feedback(self, text):
        self.bridge.setFeedback(text)

    @Slot()
    def accept(self):
        self._commit(self.compare or self.bridge.fixedComparison)

    @Slot(bool)
    def _commit(self, compare):
        if self._finished or not self.bridge.validate(compare):
            return
        self.compare = bool(compare)
        self._finish(self.Accepted)
