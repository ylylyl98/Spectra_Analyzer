"""Release transport, integrity and cancellation without a live update service."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from spectra_app.background_jobs import CancelledError
from spectra_app.updates import ReleaseClient, UpdateError, launch_installer, stable_version, trusted_asset_url


class Context:
    def __init__(self):
        self.cancelled = False
        self.messages = []

    def check_cancelled(self):
        if self.cancelled:
            raise CancelledError()

    def report(self, value):
        self.check_cancelled()
        self.messages.append(value)


class Response(io.BytesIO):
    def __init__(self, payload, url):
        super().__init__(payload)
        self.url = url
        self.headers = {'Content-Length': str(len(payload))}

    def geturl(self):
        return self.url


class ReleaseFixture:
    repo = 'example/SpectraReleases'
    asset = 'SpectraAnalyzer-0.2.0-Setup.exe'
    url = f'https://github.com/{repo}/releases/download/v0.2.0/{asset}'
    payload = b'MZ deterministic installer fixture'

    def __init__(self, *, digest=True):
        self.documents = {}
        asset = {'name': self.asset, 'browser_download_url': self.url, 'size': len(self.payload)}
        if digest:
            asset['digest'] = 'sha256:' + hashlib.sha256(self.payload).hexdigest()
        self.metadata = {'tag_name': 'v0.2.0', 'body': '<b>Literal release notes</b>',
                         'draft': False, 'prerelease': False, 'assets': [asset]}
        self.documents[self.url] = self.payload
        self.requests = []

    def open(self, request, *, timeout):
        url = request.full_url
        self.requests.append(url)
        payload = json.dumps(self.metadata).encode() if url.endswith('/releases/latest') else self.documents[url]
        return Response(payload, url)


class UpdateTransportTests(unittest.TestCase):
    def test_versions_are_strict_stable_versions(self):
        self.assertLess(stable_version('v0.9.9'), stable_version('0.10.0'))
        for value in ('1.0', '01.2.3', '1.2.3rc1', '1.2.3.4', 'v1.0.0-beta', '../1.0.0'):
            with self.subTest(value=value), self.assertRaises(UpdateError):
                stable_version(value)

    def test_release_and_verified_download_use_expected_repo_and_asset(self):
        fixture = ReleaseFixture()
        client = ReleaseClient(fixture.repo, opener=fixture)
        release = client.check('0.1.0', Context())
        self.assertEqual(release.notes, '<b>Literal release notes</b>')
        with tempfile.TemporaryDirectory() as directory:
            installer = client.download(release, Path(directory), Context())
            self.assertEqual(installer.path.read_bytes(), fixture.payload)
            installer.verify()
            installer.path.write_bytes(b'changed after download')
            with self.assertRaises(UpdateError):
                installer.verify()
        self.assertEqual(fixture.requests[0], f'https://api.github.com/repos/{fixture.repo}/releases/latest')

    def test_current_version_needs_no_download(self):
        fixture = ReleaseFixture()
        self.assertIsNone(ReleaseClient(fixture.repo, opener=fixture).check('0.2.0', Context()))
        self.assertEqual(len(fixture.requests), 1)

    def test_installer_process_is_started_only_after_final_integrity_check(self):
        fixture = ReleaseFixture()
        client = ReleaseClient(fixture.repo, opener=fixture)
        release = client.check('0.1.0', Context())
        with tempfile.TemporaryDirectory() as directory:
            installer = client.download(release, Path(directory), Context())
            with patch('spectra_app.updates.sys.frozen', True, create=True), patch(
                    'spectra_app.updates.sys.platform', 'win32'), patch('spectra_app.updates.subprocess.Popen') as process:
                launch_installer(installer)
                self.assertEqual(process.call_args.args[0], [str(installer.path),
                    '/CURRENTUSER', '/NORESTART', '/CLOSEAPPLICATIONS'])
                installer.path.write_bytes(b'tampered')
                with self.assertRaises(UpdateError):
                    launch_installer(installer)
                process.assert_called_once()

    def test_checksum_asset_is_supported_and_installer_mismatch_is_removed(self):
        fixture = ReleaseFixture(digest=False)
        checksum_url = fixture.url + '.sha256'
        fixture.metadata['assets'].append({'name': fixture.asset + '.sha256', 'browser_download_url': checksum_url})
        fixture.documents[checksum_url] = (hashlib.sha256(fixture.payload).hexdigest() + '  ' + fixture.asset + '\n').encode()
        client = ReleaseClient(fixture.repo, opener=fixture)
        release = client.check('0.1.0', Context())
        fixture.documents[fixture.url] = b'wrong contents'
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(UpdateError):
                client.download(release, Path(directory), Context())
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_cancelled_and_oversized_downloads_leave_no_partial_file(self):
        fixture = ReleaseFixture()
        client = ReleaseClient(fixture.repo, opener=fixture)
        release = client.check('0.1.0', Context())
        for cancelled in (True, False):
            context = Context()
            context.cancelled = cancelled
            with tempfile.TemporaryDirectory() as directory, patch('spectra_app.updates.MAX_INSTALLER_BYTES', 4):
                with self.assertRaises((CancelledError, UpdateError)):
                    client.download(release, Path(directory), context)
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_no_checksum_or_untrusted_url_cannot_be_downloaded(self):
        fixture = ReleaseFixture(digest=False)
        with self.assertRaisesRegex(UpdateError, 'SHA-256'):
            ReleaseClient(fixture.repo, opener=fixture).check('0.1.0', Context())
        for url in ('http://github.com/example/SpectraReleases/releases/download/v0.2.0/a.exe',
                    'https://github.com/other/repo/releases/download/v0.2.0/a.exe',
                    'https://github.com.evil.example/example/SpectraReleases/releases/download/v0.2.0/a.exe',
                    'https://github.com@example.com/a.exe', 'https://github.com:444/example/SpectraReleases/a.exe'):
            with self.subTest(url=url), self.assertRaises(UpdateError):
                trusted_asset_url(url, fixture.repo)

    def test_offline_and_unpublished_channel_have_clear_messages(self):
        fixture = ReleaseFixture()
        client = ReleaseClient(fixture.repo, opener=fixture)
        for error, message in ((URLError('offline'), 'internet connection'),
                (HTTPError('fixture', 404, 'Not Found', {}, None), 'No published update')):
            with patch.object(fixture, 'open', side_effect=error), self.assertRaisesRegex(UpdateError, message):
                client.check('0.1.0', Context())

    def test_mid_download_cancellation_and_deadline_remove_partial_file(self):
        fixture = ReleaseFixture()
        client = ReleaseClient(fixture.repo, opener=fixture)
        release = client.check('0.1.0', Context())
        class CancelOnProgress(Context):
            def report(self, message):
                super().report(message)
                self.cancelled = True
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(CancelledError):
                client.download(release, Path(directory), CancelOnProgress())
            self.assertEqual(list(Path(directory).iterdir()), [])
        with tempfile.TemporaryDirectory() as directory, patch('spectra_app.updates.time.monotonic', side_effect=[0, 99999]):
            with self.assertRaisesRegex(UpdateError, 'too long'):
                client.download(release, Path(directory), Context())
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_redirect_validation_precedes_contact_with_redirect_target(self):
        from spectra_app.updates import TrustedRedirects
        handler = TrustedRedirects(ReleaseFixture.repo)
        from urllib.request import Request
        request = Request(ReleaseFixture.url)
        with self.assertRaises(UpdateError):
            handler.redirect_request(request, None, 302, 'redirect', {}, 'https://evil.example/payload.exe')
        redirected = handler.redirect_request(request, None, 302, 'redirect', {},
            'https://release-assets.githubusercontent.com/github-production-release-asset/123/fixture')
        self.assertIn('release-assets.githubusercontent.com', redirected.full_url)


if __name__ == '__main__':
    unittest.main()
