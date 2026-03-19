import subprocess
import sys
from pathlib import Path


def test_selfcheck_script_reports_no_errors():
    repo_root = Path(__file__).resolve().parents[1]
    proc = subprocess.run([sys.executable, 'selfcheck.py'], cwd=repo_root, capture_output=True, text=True, check=False)
    assert proc.returncode == 0
    assert '[ERROR]' not in proc.stdout
