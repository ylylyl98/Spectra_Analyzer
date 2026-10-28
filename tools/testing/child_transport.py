"""Observe subprocess transport inside one module worker, never the supervisor."""
import json
import os
from pathlib import Path
import subprocess
import time


def install_recorder(folder):
    original_popen = subprocess.Popen
    children = folder / 'children'
    children.mkdir()
    sequence = 0

    class RecordedPopen(original_popen):
        def save(self):
            (self.evidence / 'process.json').write_text(
                json.dumps(self.record, indent=2, default=str), encoding='utf-8')

        def __init__(self, *args, **kwargs):
            nonlocal sequence
            sequence += 1
            self.evidence = children / ('child-%03d' % sequence)
            self.evidence.mkdir()
            self.record = dict(command=args[0] if args else kwargs.get('args'),
                               started_time=time.time(),
                               case_id=(kwargs.get('env') or os.environ).get('SPECTRA_QUICK_MIGRATION_CASE'),
                               pipes_requested=any(kwargs.get(key) == subprocess.PIPE for key in ('stdout', 'stderr', 'stdin')))
            try:
                super().__init__(*args, **kwargs)
            except BaseException as error:
                self.record['launch_error'] = repr(error)
                self.save()
                raise
            self.record['pid'] = self.pid
            self.save()

        def wait(self, *args, **kwargs):
            result = super().wait(*args, **kwargs)
            self.record.update(exit_code=result, actual_exit_observed=True, exit_observed_time=time.time())
            self.save()
            return result

        def communicate(self, *args, **kwargs):
            try:
                output, errors = super().communicate(*args, **kwargs)
            except BaseException as error:
                self.record['communicate_error'] = repr(error)
                self.save()
                raise
            for name, value in (('stdout.log', output), ('stderr.log', errors)):
                if isinstance(value, str):
                    (self.evidence / name).write_text(value, encoding='utf-8', newline='')
                elif value is not None:
                    (self.evidence / name).write_bytes(value)
            command = self.record['command']
            if self.record['case_id'] and isinstance(command, (tuple, list)):
                payload = Path(command[-1])
                if payload.is_file():
                    (self.evidence / 'result.json').write_bytes(payload.read_bytes())
            self.record.update(exit_code=self.returncode, actual_exit_observed=self.returncode is not None,
                               communicate_completed=True, completed_time=time.time())
            self.save()
            return output, errors

    subprocess.Popen = RecordedPopen
