import unittest

from tests.quick_migration_case import _has_unexpected_stderr


class QuickMigrationDiagnosticsTests(unittest.TestCase):
    def test_only_exact_font_cache_notice_is_accepted(self):
        notice = 'Matplotlib is building the font cache; this may take a moment.'
        for stderr in ('', notice, notice + '\n', notice + '\r\n'):
            with self.subTest(stderr=stderr):
                self.assertFalse(_has_unexpected_stderr(stderr))

        for stderr in ('\n', 'Qt warning\n', notice + '\nQt warning\n',
                       'Qt warning\n' + notice + '\n', 'prefix ' + notice + '\n'):
            with self.subTest(stderr=stderr):
                self.assertTrue(_has_unexpected_stderr(stderr))
