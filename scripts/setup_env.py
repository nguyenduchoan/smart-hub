#!/usr/bin/env python3
"""Install only into this project's venv; works without Debian ensurepip."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.request
import venv

ROOT = Path(__file__).resolve().parents[1]
PIP_URL = (
    "https://files.pythonhosted.org/packages/f3/6e/"
    "1736e5b4ae2b778ef2f81c47d797de9f891d4d8acb047a24ca37a60294dd/"
    "pip-26.2.1-py3-none-any.whl"
)
PIP_SHA256 = "71138adf1f4ca900cdb7d289c21b7494329f2332b6d85f0e1c42108c0384ed3e"


def main():
    if sys.version_info < (3, 11):
        raise RuntimeError("Cần Python >= 3.11; đã kiểm thử trên Debian 12/Python 3.11.")
    print("Chỉ cài trong smart-hub/.venv:", flush=True)
    print("  pip: công cụ cài package (bootstrap wheel có SHA-256).", flush=True)
    print("  numpy: xử lý audio và vector; onnxruntime: chạy model INT8 một luồng CPU.", flush=True)
    print("  Runtime kéo theo flatbuffers, protobuf, packaging, sympy/mpmath, coloredlogs/humanfriendly.", flush=True)
    print("  Capture dùng arecord đã có; không cài framework ML/audio khác.", flush=True)
    target = ROOT / ".venv"
    if target.is_symlink():
        raise RuntimeError(".venv phải là thư mục trong project, không phải symlink.")
    if target.exists() and not (target / "pyvenv.cfg").is_file():
        raise RuntimeError(".venv đã tồn tại nhưng không phải virtual environment.")
    venv.EnvBuilder(with_pip=False).create(target)
    python = target / "bin/python"
    with tempfile.TemporaryDirectory(prefix="smart-hub-pip-") as temp:
        wheel = Path(temp) / "pip-26.2.1-py3-none-any.whl"
        with urllib.request.urlopen(PIP_URL, timeout=30) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != PIP_SHA256:
            raise RuntimeError("Checksum pip không khớp; dừng cài đặt.")
        wheel.write_bytes(data)
        env = os.environ.copy()
        env["PYTHONPATH"] = str(wheel)
        subprocess.run(
            [str(python), "-m", "pip", "--isolated", "--disable-pip-version-check",
             "install", "--only-binary=:all:", "--no-cache-dir",
             "--index-url", "https://pypi.org/simple", str(wheel),
             "-r", str(ROOT / "requirements.txt")],
            env=env, check=True,
        )
    print("PASS: dependency đã cài trong .venv.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
