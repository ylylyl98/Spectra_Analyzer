import time
from PySide6.QtTest import QTest


def wait_jobs(window, timeout=5000):
    deadline = time.monotonic() + timeout / 1000
    while window._background_jobs.busy and time.monotonic() < deadline:
        QTest.qWait(10)
    if window._background_jobs.busy:
        raise AssertionError('background job did not finish')
    QTest.qWait(10)

