"""Development runner; Docker Compose is the production entry point."""
import os
from pathlib import Path
import shlex
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
for line in (root / ".env").read_text().splitlines():
    if not line.strip() or line.lstrip().startswith("#"):
        continue
    key, value = line.split("=", 1)
    parsed = shlex.split(value, comments=True)
    os.environ.setdefault(key.strip(), parsed[0] if parsed else "")
os.chdir(root)
host = os.environ.get("HOST", "0.0.0.0")
port = os.environ.get("PORT", "8787")
raise SystemExit(subprocess.call([sys.executable, "-m", "uvicorn", "app.main:factory", "--factory",
                                "--host", host, "--port", port, "--no-access-log"]))
