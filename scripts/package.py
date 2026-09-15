"""Build a source deployment archive from an explicit allowlist; no credentials/data."""
from pathlib import Path
import hashlib
import tarfile

root = Path(__file__).resolve().parents[1]
out = root / "dist"
out.mkdir(exist_ok=True)
roots = ["README.md", "Dockerfile", "compose.yaml", ".env.example", ".gitignore", ".dockerignore",
         "requirements.txt", "requirements-dev.txt"]
files = [root / name for name in roots]
patterns = {"nginx": ["*.conf"], "app": ["*.py", "*.js", "*.css", "*.html", "*.mp3"], "scripts": ["*.py"],
            "tests": ["*.py"], "docs": ["*.md"], "whatsapp": ["*.mjs", "package*.json", "Dockerfile", ".dockerignore"]}
for directory, globs in patterns.items():
    for pattern in globs:
        for file in (root / directory).rglob(pattern):
            if "node_modules" not in file.parts and "__pycache__" not in file.parts:
                files.append(file)
archive = out / "oncall-v0.1.tar.gz"
with tarfile.open(archive, "w:gz") as tar:
    for file in sorted(set(files)):
        tar.add(file, arcname=str(Path("oncall") / file.relative_to(root)), recursive=False)
with tarfile.open(archive) as tar:
    assert all(not any(part in {".env", "data", ".git", "node_modules"} for part in Path(name).parts)
               for name in tar.getnames())
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
(out / "oncall-v0.1.tar.gz.sha256").write_text(digest + "  " + archive.name + "\n")
print(f"已打包 {len(set(files))} 个源码文件：{archive}")
print("已检查：不包含 .env、账号会话、聊天数据或 node_modules。")
