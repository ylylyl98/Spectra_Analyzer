"""Inspect installed build metadata before trusting a successful pip-check stamp."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import sys


def inspect_environment(requirements_path):
    def normalized(name):
        return re.sub(r'[-_.]+', '-', name).lower()

    locked = {}
    for line in Path(requirements_path).read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'([A-Za-z0-9_.-]+)==([^\s;]+)', line)
        if not match:
            raise ValueError(f'Build requirements must use exact name==version pins: {line}')
        name, version = match.groups()
        key = normalized(name)
        if key in locked:
            raise ValueError(f'Duplicate build requirement: {name}')
        locked[key] = version

    installed = []
    versions = {}
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata['Name']
        if not name:
            raise ValueError('An installed distribution has no Name metadata.')
        key = normalized(name)
        versions.setdefault(key, []).append(distribution.version)
        metadata = '\0'.join(distribution.read_text(filename) or ''
                             for filename in ('METADATA', 'RECORD', 'direct_url.json'))
        installed.append(dict(name=name, version=distribution.version,
                              location=str(Path(distribution.locate_file('')).resolve()),
                              metadataSha256=hashlib.sha256(metadata.encode('utf-8')).hexdigest()))
    installed.sort(key=lambda item: (normalized(item['name']), item['version'],
                                     item['location'], item['metadataSha256']))
    fingerprint = hashlib.sha256(json.dumps(installed, sort_keys=True).encode('utf-8')).hexdigest()
    return dict(lockedVersionsMatch=all(versions.get(name) == [version]
                                       for name, version in locked.items()),
                fingerprint=fingerprint,
                dependencies=[f"{item['name']}=={item['version']}" for item in installed])


if __name__ == '__main__':
    print(json.dumps(inspect_environment(sys.argv[1])))
