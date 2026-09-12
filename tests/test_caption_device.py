"""Runs the worker's own device-decision checks (tests/caption_device_check.py) under pytest."""

import os
import subprocess
import sys


def test_caption_device_checks_pass():
    script = os.path.join(os.path.dirname(__file__), "caption_device_check.py")
    done = subprocess.run([sys.executable, script], capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "all caption-device checks pass" in done.stdout
