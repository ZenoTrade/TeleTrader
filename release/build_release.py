"""Build the Windows release of Zeno Copier (run on Windows).

    python release/build_release.py --public-key <base64> --server https://api.<domain>

Embeds the license server's public key and URL, then builds three
executables with PyInstaller into dist/ZenoCopier/ and zips them.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUARD = ROOT / "tradebot" / "infrastructure" / "license_guard.py"
CONFIG = ROOT / "config.py"
WIZARD = ROOT / "setup_wizard.py"

APPS = [
    ("ZenoCopier", "main.py", True),       # console: shows the live log
    ("ZenoAdmin", "admin_panel.py", False),
    ("ZenoSetup", "setup_wizard.py", False),
]


def embed(public_key: str, server: str) -> None:
    text = GUARD.read_text()
    GUARD.write_text(re.sub(r'^EMBEDDED_PUBLIC_KEY = ".*"$', f'EMBEDDED_PUBLIC_KEY = "{public_key}"',
                            text, flags=re.M))
    for path in (CONFIG, WIZARD):
        path.write_text(path.read_text().replace("https://api.zenotrade.example", server))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--public-key", required=True)
    p.add_argument("--server", required=True)
    p.add_argument("--version", default="dev")
    a = p.parse_args()

    embed(a.public_key, a.server.rstrip("/"))
    dist = ROOT / "dist" / "ZenoCopier"
    shutil.rmtree(ROOT / "dist", ignore_errors=True)
    for name, script, console in APPS:
        subprocess.run([
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile",
            "--name", name, "--distpath", str(dist), "--workpath", str(ROOT / "release" / "work"),
            "--console" if console else "--windowed",
            "--collect-submodules", "tradebot",
            str(ROOT / script),
        ], check=True, cwd=ROOT)
    shutil.copy(ROOT / "release" / "README-customer.txt", dist / "README.txt")
    archive = shutil.make_archive(str(ROOT / "dist" / f"ZenoCopier-{a.version}"), "zip", dist)
    print(f"Built {archive}")


if __name__ == "__main__":
    main()
