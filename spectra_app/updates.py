"""Bounded GitHub release discovery and verified installer downloads."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .app_version import installer_asset_name

MAX_INSTALLER_BYTES = 1024 * 1024 * 1024
MAX_METADATA_BYTES = 2 * 1024 * 1024
REQUEST_TIMEOUT = 10
DOWNLOAD_SECONDS = 30 * 60
_REPOSITORY = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z')
_VERSION = re.compile(r'v?(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\Z')
_SHA256 = re.compile(r'[0-9a-fA-F]{64}\Z')


class UpdateError(Exception):
    """An update cannot be safely offered or installed."""


def stable_version(value: str) -> tuple[int, int, int]:
    match = _VERSION.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise UpdateError('The update has an unsupported version number.')
    return tuple(map(int, match.groups()))


def _url_parts(url):
    try:
        parts = urlsplit(url)
        if (parts.scheme != 'https' or parts.username or parts.password
                or parts.port not in (None, 443) or parts.fragment):
            raise ValueError()
        return parts
    except (ValueError, TypeError):
        raise UpdateError('The release contains an untrusted download address.') from None


def trusted_asset_url(url: str, repository: str, *, redirected=False) -> str:
    parts = _url_parts(url)
    if parts.hostname == 'github.com':
        if parts.path.startswith(f'/{repository}/releases/download/') and not parts.query:
            return url
    elif redirected and parts.hostname in ('release-assets.githubusercontent.com', 'objects.githubusercontent.com'):
        if parts.path.startswith('/github-production-release-asset/'):
            return url
    raise UpdateError('The release contains an untrusted download address.')


class TrustedRedirects(HTTPRedirectHandler):
    def __init__(self, repository):
        super().__init__()
        self.repository = repository

    def redirect_request(self, request, fp, code, message, headers, newurl):
        # Check before urllib makes any connection to the redirect destination.
        trusted_asset_url(newurl, self.repository, redirected=True)
        if urlsplit(request.full_url).hostname == 'api.github.com':
            raise UpdateError('The release service redirected outside its configured address.')
        return super().redirect_request(request, fp, code, message, headers, newurl)


@dataclass(frozen=True)
class Release:
    version: str
    notes: str
    asset_name: str
    url: str
    sha256: str
    size: int


@dataclass(frozen=True)
class VerifiedInstaller:
    path: Path
    sha256: str
    size: int

    def verify(self):
        if not self.path.is_file() or self.path.stat().st_size != self.size:
            raise UpdateError('The downloaded installer changed. Download it again.')
        digest = hashlib.sha256()
        with self.path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(64 * 1024), b''):
                digest.update(chunk)
        if digest.hexdigest() != self.sha256:
            raise UpdateError('The downloaded installer failed SHA-256 verification. Download it again.')


class ReleaseClient:
    def __init__(self, repository: str, *, opener=None):
        self.repository = repository
        self.opener = opener or build_opener(TrustedRedirects(repository))

    def _open(self, url, *, metadata=False):
        if metadata:
            if url != f'https://api.github.com/repos/{self.repository}/releases/latest':
                raise UpdateError('The release service address is invalid.')
        else:
            trusted_asset_url(url, self.repository)
        request = Request(url, headers={'User-Agent': 'SpectraAnalyzer-Updater',
                                       'Accept': 'application/vnd.github+json' if metadata else 'application/octet-stream'})
        try:
            response = self.opener.open(request, timeout=REQUEST_TIMEOUT)
        except HTTPError as exc:
            code = exc.code
            exc.close()
            if code == 404:
                raise UpdateError('No published update is available from this release channel.') from None
            if code in (401, 403):
                raise UpdateError('The release channel is unavailable or requires access. Try again later.') from None
            raise UpdateError(f'The release service returned HTTP {code}. Try again later.') from None
        except (URLError, TimeoutError, OSError):
            raise UpdateError('Could not reach the update service. Check your internet connection and try again.') from None
        try:
            if metadata and response.geturl() != url:
                raise UpdateError('The release service redirected outside its configured address.')
            if not metadata:
                trusted_asset_url(response.geturl(), self.repository, redirected=True)
        except Exception:
            response.close()
            raise
        return response

    def _read_small(self, url, context, *, metadata=False, limit=MAX_METADATA_BYTES):
        deadline = time.monotonic() + 30
        content = bytearray()
        with self._open(url, metadata=metadata) as stream:
            while True:
                context.check_cancelled()
                if time.monotonic() > deadline:
                    raise UpdateError('The update service took too long to respond. Try again later.')
                # read1 returns after one buffered socket read, so a server
                # trickling bytes cannot bypass the overall operation deadline.
                read = getattr(stream, 'read1', stream.read)
                chunk = read(min(64 * 1024, limit + 1 - len(content)))
                if not chunk:
                    return bytes(content)
                content.extend(chunk)
                if len(content) > limit:
                    raise UpdateError('The release metadata exceeds the supported size.')

    def check(self, current_version, context) -> Release | None:
        if not self.repository:
            raise UpdateError('An update channel has not been configured for this build.')
        if not _REPOSITORY.fullmatch(self.repository) or any(p in ('.', '..') for p in self.repository.split('/')):
            raise UpdateError('The configured update channel is invalid.')
        current = stable_version(current_version)
        context.report('Checking for updates…')
        try:
            metadata = json.loads(self._read_small(
                f'https://api.github.com/repos/{self.repository}/releases/latest', context, metadata=True))
        except (ValueError, UnicodeError):
            raise UpdateError('The update service returned invalid release metadata.') from None
        if not isinstance(metadata, dict):
            raise UpdateError('The update service returned invalid release metadata.')
        tag = metadata.get('tag_name')
        version = stable_version(tag)
        if metadata.get('draft') or metadata.get('prerelease'):
            raise UpdateError('This release is not a stable application update.')
        if version <= current:
            return None
        version_text = '.'.join(map(str, version))
        name = installer_asset_name(version_text)
        assets = metadata.get('assets', [])
        if not isinstance(assets, list):
            raise UpdateError('The release has no supported Windows installer.')
        matches = [asset for asset in assets if isinstance(asset, dict) and asset.get('name') == name]
        if len(matches) != 1:
            raise UpdateError('The release has no supported Windows installer.')
        asset = matches[0]
        url = trusted_asset_url(asset.get('browser_download_url', ''), self.repository)
        if urlsplit(url).path != f'/{self.repository}/releases/download/{tag}/{name}':
            raise UpdateError('The installer address does not match the release version.')
        size = asset.get('size')
        if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_INSTALLER_BYTES:
            raise UpdateError('The installer exceeds the supported size or has invalid size metadata.')
        digest = asset.get('digest') or ''
        if not isinstance(digest, str):
            raise UpdateError('The installer SHA-256 checksum is invalid.')
        if digest.startswith('sha256:') and _SHA256.fullmatch(digest[7:]):
            checksum = digest[7:].lower()
        else:
            checksums = [item for item in assets if isinstance(item, dict) and item.get('name') == name + '.sha256']
            if len(checksums) != 1:
                raise UpdateError('This release has no valid SHA-256 checksum for its installer.')
            checksum_url = trusted_asset_url(checksums[0].get('browser_download_url', ''), self.repository)
            if urlsplit(checksum_url).path != urlsplit(url).path + '.sha256':
                raise UpdateError('The checksum address does not match the installer.')
            try:
                fields = self._read_small(checksum_url, context, limit=4096).decode('utf-8').strip().split()
            except UnicodeError:
                raise UpdateError('The installer SHA-256 checksum is invalid.') from None
            if (len(fields) not in (1, 2) or not _SHA256.fullmatch(fields[0])
                    or (len(fields) == 2 and fields[1].lstrip('*') != name)):
                raise UpdateError('The installer SHA-256 checksum is invalid.')
            checksum = fields[0].lower()
        notes = metadata.get('body') or 'No release notes were provided.'
        if not isinstance(notes, str):
            raise UpdateError('The release notes are invalid.')
        return Release(version_text, notes, name, url, checksum, size)

    def download(self, release: Release, directory: Path, context) -> VerifiedInstaller:
        destination = Path(directory) / installer_asset_name(release.version)
        partial = destination.with_suffix('.exe.part')
        digest = hashlib.sha256()
        total = 0
        previous_percent = -1
        deadline = time.monotonic() + DOWNLOAD_SECONDS
        try:
            context.check_cancelled()
            if release.size > MAX_INSTALLER_BYTES:
                raise UpdateError('The installer exceeds the supported size.')
            with self._open(release.url) as stream, partial.open('xb') as output:
                while True:
                    context.check_cancelled()
                    if time.monotonic() > deadline:
                        raise UpdateError('The installer download took too long. Try again later.')
                    read = getattr(stream, 'read1', stream.read)
                    chunk = read(64 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > min(release.size, MAX_INSTALLER_BYTES):
                        raise UpdateError('The installer download exceeds its expected size.')
                    output.write(chunk)
                    digest.update(chunk)
                    percent = total * 100 // release.size
                    if percent != previous_percent:
                        context.report(f'Downloading update… {percent}%')
                        previous_percent = percent
            context.check_cancelled()
            if total != release.size or digest.hexdigest() != release.sha256:
                raise UpdateError('The installer failed SHA-256 verification. Nothing was installed.')
            partial.replace(destination)
            return VerifiedInstaller(destination, release.sha256, total)
        except Exception:
            partial.unlink(missing_ok=True)
            raise


def launch_installer(installer: VerifiedInstaller):
    if sys.platform != 'win32' or not getattr(sys, 'frozen', False):
        raise UpdateError('Install updates from the installed Windows application.')
    installer.verify()
    subprocess.Popen([str(installer.path), '/CURRENTUSER', '/NORESTART', '/CLOSEAPPLICATIONS'],
                     close_fds=True, cwd=str(installer.path.parent))
