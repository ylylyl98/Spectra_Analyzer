"""The production registry needs no QWidget or native frame to own documents."""
import unittest

from PySide6.QtCore import QObject, QRect

from spectra_app.document_registry import DocumentRegistry


class DocumentRegistryTests(unittest.TestCase):
    def registry(self):
        registry = DocumentRegistry(lambda key: self.select(registry, key), lambda key, document: True)
        return registry

    @staticmethod
    def select(registry, key):
        registry.activated(key)
        return True

    def add(self, registry, key=('dataset', 'graph')):
        document = QObject()
        registry.register(key, document)
        registry.opened(key, QRect(10, 20, 300, 240))
        return document

    def test_identity_and_revocable_lifetime_without_widget_ownership(self):
        registry, other = self.registry(), self.registry()
        key = ('dataset', 'graph')
        document = self.add(registry, key)
        self.add(other, key)
        lease = registry.capture_input(key)
        self.assertTrue(registry.authorize(lease))
        self.assertFalse(other.authorize(lease))
        self.assertIsNone(document.parent())
        registry.closed(key)
        self.assertFalse(registry.authorize(lease))
        registry.opened(key, QRect())
        self.assertIs(registry.document_for(key), document)
        self.assertFalse(registry.authorize(lease))
        self.assertTrue(registry.authorize(registry.capture_input(key)))
        with self.assertRaises(ValueError):
            registry.register(key, QObject())
        registry.remove(key)
        self.add(registry, key)
        self.assertFalse(registry.authorize(lease))

    def test_activation_rechecks_identity_lifetime_eligibility_and_actual_owner(self):
        key, other = ('dataset', 'graph'), ('dataset', 'other')
        for mutation in ('replace', 'remove', 'close', 'park', 'reopen', 'different_owner',
                         'busy', 'disabled', 'unpresented'):
            with self.subTest(mutation=mutation):
                eligible = True
                registry = DocumentRegistry(lambda key: activate(key), lambda key, document: eligible)
                self.add(registry, key)
                self.add(registry, other)
                lease = registry.capture_input(key)

                def activate(requested):
                    nonlocal eligible
                    registry.activated(requested)
                    if mutation == 'replace':
                        registry.remove(key)
                        self.add(registry, key)
                        registry.activated(key)
                    elif mutation == 'remove':
                        registry.remove(key)
                    elif mutation == 'close':
                        registry.closed(key)
                    elif mutation == 'park':
                        registry.park()
                    elif mutation == 'reopen':
                        registry.closed(key)
                        registry.opened(key, QRect())
                        registry.activated(key)
                    elif mutation == 'different_owner':
                        registry.activated(other)
                    else:
                        eligible = False
                    return True

                mutations = []
                if registry.authorize(lease):
                    mutations.append('scientific edit')
                self.assertEqual(mutations, [])

    def test_inactive_displayed_owner_can_activate_but_hidden_or_closed_cannot(self):
        eligible = True
        registry = DocumentRegistry(lambda key: self.select(registry, key), lambda key, document: eligible)
        key, other = ('dataset', 'graph'), ('dataset', 'other')
        self.add(registry, key)
        self.add(registry, other)
        registry.activated(other)
        lease = registry.capture_input(key)
        self.assertTrue(registry.authorize(lease))
        self.assertEqual(registry.active_key, key)
        eligible = False
        self.assertFalse(registry.authorize(lease))
        registry.closed(key)
        self.assertFalse(registry.authorize(lease))

    def test_park_and_repeated_restore_preserve_objects_and_revoke_every_old_lease(self):
        registry = self.registry()
        key = ('dataset', 'graph')
        document = self.add(registry, key)
        lease = registry.capture_input(key)
        parked = registry.park()
        candidate = self.add(registry, key)
        self.assertIsNot(candidate, document)
        self.assertFalse(registry.authorize(lease))
        candidate_lease = registry.capture_input(key)
        registry.remove(key)
        registry.restore(parked)
        first_restored = registry.capture_input(key)
        self.assertIs(registry.document_for(key), document)
        self.assertFalse(registry.authorize(candidate_lease))
        self.assertFalse(registry.authorize(lease))
        self.assertTrue(registry.authorize(first_restored))
        registry.restore(parked)
        self.assertIs(registry.document_for(key), document)
        self.assertFalse(registry.authorize(first_restored))
        self.assertTrue(registry.authorize(registry.capture_input(key)))
        self.assertIsNone(document.parent())

    def test_snapshot_and_park_own_detached_closed_uncached_layout(self):
        registry = self.registry()
        key, closed = ('dataset', 'graph'), ('dataset', 'closed')
        document = self.add(registry, key)
        metadata = {'mode': 'tabbed', 'documents': [
            {'key': list(key), 'open': True, 'rect': [11, 22, 333, 244]},
            {'key': list(closed), 'open': False, 'rect': [44, 55, 366, 277]}],
            'active': list(key), 'stacking': [list(key)]}
        registry.restore_workspace(metadata)
        self.assertEqual(registry.workspace_snapshot((key, closed)), metadata)
        self.assertEqual(registry.open_keys(), (key,))
        self.assertEqual(registry.cached_items(), ((key, document),))
        rectangle = registry.normal_rectangle(closed)
        rectangle.moveLeft(999)
        snapshot = registry.workspace_snapshot((key, closed))
        snapshot['documents'][1]['rect'][0] = 999
        parked = registry.park()
        registry.restore(parked)
        self.assertEqual(registry.mode, 'tabbed')
        self.assertEqual(registry.normal_rectangle(closed), QRect(44, 55, 366, 277))
        self.assertEqual(registry.workspace_snapshot((key, closed)), metadata)
        self.assertFalse(registry.initial_view)


if __name__ == '__main__':
    unittest.main()
