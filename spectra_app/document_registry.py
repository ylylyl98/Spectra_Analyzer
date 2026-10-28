"""Retained document identity, layout metadata and revocable input authority.

Documents are exact QObjects owned by their real host. This registry neither
constructs frames nor reparents, displays or disposes scientific documents.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, QRect

from .workbook_state import document_record_key, encode_document_key


@dataclass(frozen=True)
class DocumentInputLease:
    registry_identity: object
    key: tuple
    registration: int
    lifetime: int


@dataclass
class _Document:
    document: QObject
    registration: int
    lifetime: int
    open: bool = False


@dataclass(frozen=True)
class ParkedRegistry:
    registry_identity: object
    documents: tuple
    rectangles: tuple
    mode: str
    active_key: tuple | None
    stacking: tuple
    initial_view: bool

    def cached_items(self):
        return tuple((key, document) for key, document, registration, opened in self.documents)


class DocumentRegistry(QObject):
    """Canonical document state, usable by native and future scene hosts alike."""

    def __init__(self, activate, eligible, parent=None):
        super().__init__(parent)
        self._activate = activate
        self._eligible = eligible
        self._identity = object()
        self._serial = 0
        self._lifetime = 0
        self._documents = {}
        self._rectangles = {}
        self._mode = 'free'
        self._active_key = None
        self._stacking = []
        self.initial_view = True

    def _next_lifetime(self):
        self._lifetime += 1
        return self._lifetime

    @property
    def active_key(self):
        return self._active_key

    @property
    def mode(self):
        return self._mode

    def document_for(self, key):
        record = self._documents.get(key)
        return record.document if record is not None else None

    def keys(self):
        return tuple(self._documents)

    def cached_items(self):
        return tuple((key, record.document) for key, record in self._documents.items())

    def cached_documents(self):
        return tuple(record.document for record in self._documents.values())

    def open_keys(self):
        return tuple(key for key, record in self._documents.items() if record.open)

    def register(self, key, document):
        if not isinstance(document, QObject):
            raise TypeError('A retained document must be a QObject.')
        existing = self._documents.get(key)
        if existing is not None:
            if existing.document is not document:
                raise ValueError('Remove the existing document before replacing its registration.')
            return
        self._serial += 1
        self._documents[key] = _Document(document, self._serial, self._next_lifetime())

    def opened(self, key, initial_rectangle):
        record = self._documents[key]
        if not record.open:
            record.lifetime = self._next_lifetime()
            record.open = True
            self._stacking.append(key)
        if key not in self._rectangles and initial_rectangle is not None:
            self.remember_rectangle(key, initial_rectangle)
        self.initial_view = False

    def activated(self, key):
        if key is not None and (key not in self._documents or not self._documents[key].open):
            raise ValueError('Only an open document can become active.')
        self._active_key = key
        if key is not None:
            self._stacking.remove(key)
            self._stacking.append(key)

    def closed(self, key):
        record = self._documents.get(key)
        if record is not None:
            record.open = False
            record.lifetime = self._next_lifetime()
        if key in self._stacking:
            self._stacking.remove(key)
        if self._active_key == key:
            self._active_key = None

    def remove(self, key):
        self.closed(key)
        record = self._documents.pop(key, None)
        self._rectangles.pop(key, None)
        return record.document if record is not None else None

    def set_mode(self, mode):
        if mode not in ('free', 'tabbed'):
            raise ValueError('Unknown document layout mode.')
        self._mode = mode

    def normal_rectangle(self, key):
        rect = self._rectangles.get(key)
        return QRect(rect) if rect is not None else None

    def remember_rectangle(self, key, rect):
        self._rectangles[key] = QRect(rect)

    def report_stacking(self, keys):
        keys = tuple(keys)
        opened = set(self.open_keys())
        if len(keys) != len(set(keys)) or set(keys) != opened:
            raise ValueError('Stacking must contain every open document exactly once.')
        self._stacking = list(keys)

    def capture_input(self, key):
        record = self._documents[key]
        return DocumentInputLease(self._identity, key, record.registration, record.lifetime)

    def revoke_input(self, key=None):
        """Retire presentation callbacks without changing document/layout state."""
        records = self._documents.values() if key is None else (self._documents.get(key),)
        for record in records:
            if record is not None:
                record.lifetime = self._next_lifetime()

    def owns_input(self, lease):
        return isinstance(lease, DocumentInputLease) and lease.registry_identity is self._identity

    def _eligible_lease(self, lease):
        if not self.owns_input(lease):
            return False
        record = self._documents.get(lease.key)
        if (record is None or not record.open or record.registration != lease.registration
                or record.lifetime != lease.lifetime or not self._eligible(lease.key, record.document)):
            return False
        return (self._documents.get(lease.key) is record and record.open
                and record.registration == lease.registration and record.lifetime == lease.lifetime)

    def authorize(self, lease):
        if not self._eligible_lease(lease) or not self._activate(lease.key):
            return False
        return self._eligible_lease(lease) and self._active_key == lease.key

    def workspace_snapshot(self, keys):
        keys = tuple(keys)
        existing = set(keys)
        opened = set(self.open_keys())
        stacking = [key for key in self._stacking if key in existing]
        return {'mode': self._mode,
                'documents': [{'key': encode_document_key(key), 'open': key in opened,
                    'rect': list(self._rectangles[key].getRect()) if key in self._rectangles else None}
                    for key in keys],
                'active': list(self._active_key) if self._active_key in stacking else None,
                'stacking': [list(key) for key in stacking]}

    def restore_workspace(self, metadata):
        """Install previously validated metadata; the host presents its frames."""
        for key in self.open_keys():
            self.closed(key)
        self._rectangles = {document_record_key(record): QRect(*record['rect'])
                            for record in metadata['documents'] if record['rect'] is not None}
        for identity in metadata['stacking']:
            key = tuple(identity)
            self.opened(key, self.normal_rectangle(key))
        self.set_mode(metadata['mode'])
        self._active_key = tuple(metadata['active']) if metadata['active'] else None
        self._stacking = [tuple(key) for key in metadata['stacking']]
        self.initial_view = False

    def reset_layout(self):
        self._rectangles.clear()
        self._active_key = None
        self._mode = 'free'
        self.initial_view = True

    def park(self):
        state = ParkedRegistry(self._identity,
            tuple((key, record.document, record.registration, record.open)
                  for key, record in self._documents.items()),
            tuple((key, tuple(rect.getRect())) for key, rect in self._rectangles.items()),
            self._mode, self._active_key, tuple(self._stacking), self.initial_view)
        for key in self.keys():
            self.closed(key)
        self._documents.clear()
        self._rectangles.clear()
        self._active_key = None
        self._stacking.clear()
        return state

    def restore(self, state):
        if state.registry_identity is not self._identity:
            raise ValueError('Parked documents belong to a different registry.')
        restored = dict(state.cached_items())
        if any(restored.get(key) is not record.document for key, record in self._documents.items()):
            raise ValueError('Remove candidate documents before restoring parked documents.')
        self._documents = {key: _Document(document, registration, self._next_lifetime(), opened)
                           for key, document, registration, opened in state.documents}
        self._rectangles = {key: QRect(*rect) for key, rect in state.rectangles}
        self._mode, self._active_key = state.mode, state.active_key
        self._stacking = list(state.stacking)
        self.initial_view = state.initial_view
