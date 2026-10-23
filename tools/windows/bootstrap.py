"""Run the source app under pythonw with useful startup failure diagnostics."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
import runpy
import sys
import traceback


def show_error(message: str) -> None:
    if sys.platform == 'win32':
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, 'Spectra Analyzer could not start', 0x10)
    elif sys.stderr is not None:
        print(message, file=sys.stderr)


def run(entry: Path, log_path: Path, arguments: list[str]) -> int:
    previous_argv, previous_path = sys.argv, sys.path[:]
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # Bound retained diagnostics without changing any scientific data/settings.
        if log_path.exists() and log_path.stat().st_size > 2_000_000:
            log_path.replace(log_path.with_suffix('.previous.log'))
        with log_path.open('a', encoding='utf-8', buffering=1) as log:
            print(f'\n[{datetime.now().isoformat(timespec="seconds")}] Starting Spectra Analyzer', file=log)
            with redirect_stdout(log), redirect_stderr(log):
                sys.argv = [str(entry), *arguments]
                sys.path.insert(0, str(entry.parent))
                try:
                    runpy.run_path(str(entry), run_name='__main__')
                except SystemExit as error:
                    if error.code is None or error.code == 0:
                        return 0
                    raise
                except BaseException:
                    traceback.print_exc()
                    raise
        return 0
    except BaseException as error:
        show_error(f'{type(error).__name__}: {error}\n\nDetails: {log_path}\n\n'
                   'Check that the source folder and .venv still exist. '
                   'If you moved the source folder, rerun tools\\windows\\Install-Launcher.ps1 there.')
        return 1
    finally:
        sys.argv = previous_argv
        sys.path[:] = previous_path


if __name__ == '__main__':
    if len(sys.argv) < 3:
        show_error('The launcher configuration is incomplete. Rerun tools\\windows\\Install-Launcher.ps1.')
        raise SystemExit(1)
    raise SystemExit(run(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3:]))
