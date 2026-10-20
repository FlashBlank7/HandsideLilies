import json
import os
from pathlib import Path
import subprocess
import sys

def test_shared_icon_menu_offscreen():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable,str(root/"scripts/verify_desktop_icon_layer.py")],
        cwd=root,capture_output=True,text=True,encoding="utf-8",timeout=25,
        env={**os.environ,"PYTHONUTF8":"1","PYTHONDONTWRITEBYTECODE":"1"},
        creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    assert result.returncode == 0, result.stdout+result.stderr
    assert json.loads(result.stdout)["passed"]
