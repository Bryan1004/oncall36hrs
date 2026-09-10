"""Create local settings; never overwrite existing credentials."""
from pathlib import Path
import os
import secrets

root = Path(__file__).resolve().parents[1]
target = root / ".env"
if target.exists():
    raise SystemExit(".env 已存在，未覆盖。")
data = (root / ".env.example").read_text()
data = data.replace("ADMIN_PASSWORD=\n", f"ADMIN_PASSWORD={secrets.token_urlsafe(24)}\n")
data = data.replace("BRIDGE_TOKEN=\n", f"BRIDGE_TOKEN={secrets.token_urlsafe(32)}\n")
fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as out:
    out.write(data)
print("已创建 .env（权限 600）。请在本机编辑凭据，不要发到聊天中。")
