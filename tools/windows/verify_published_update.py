"""GitHub release-job check through the real anonymous updater client."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from spectra_app.updates import ReleaseClient


class Context:
    def check_cancelled(self):
        pass

    def report(self, message):
        print(message, flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repository', required=True)
    parser.add_argument('--previous-version', required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    report = dict(status='failed', version=args.version, previousVersion=args.previous_version)
    try:
        client, context = ReleaseClient(args.repository), Context()
        for attempt in range(6):
            try:
                release = client.check(args.previous_version, context)
                if release is None or release.version != args.version:
                    raise RuntimeError('Public latest release has not propagated to the target version')
                break
            except Exception:
                if attempt == 5:
                    raise
                time.sleep(10)
        if release.sha256 != args.sha256.lower():
            raise RuntimeError('Public updater digest differs from the built installer')
        downloaded = client.download(release, args.output, context)
        downloaded.verify()
        report.update(status='passed', sha256=downloaded.sha256, bytes=downloaded.size,
                      url=release.url, anonymous=True)
    except Exception as exc:
        report['error'] = str(exc)
        raise
    finally:
        (args.output / 'published-update.json').write_text(json.dumps(report, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
