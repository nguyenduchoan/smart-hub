#!/usr/bin/env python3
"""Explicit online setup for the ignored Pipecat probe environment only."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import urllib.parse
import urllib.request
import venv

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / ".experiments/phase1b"


def get(url):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ("pypi.org", "files.pythonhosted.org"):
        raise ValueError("Chỉ tải package từ PyPI qua HTTPS.")
    with urllib.request.urlopen(url, timeout=90) as response:
        return response.read()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--report", type=Path, help="pip --dry-run --report JSON")
    selection.add_argument("--manifest", type=Path, help="JSON benchmark đã khóa archive/checksum")
    args = parser.parse_args()
    if args.manifest:
        entries = json.loads(args.manifest.read_text())["dependencies"]["archives"]
    else:
        report = json.loads(args.report.read_text())
        entries = [{"name": entry["metadata"]["name"], "version": entry["metadata"]["version"],
                    "url": entry["download_info"]["url"],
                    "sha256": entry["download_info"]["archive_info"]["hashes"]["sha256"]}
                   for entry in report["install"]]
    if not any(x["name"] == "pipecat-ai" and x["version"] == "1.10.0" for x in entries):
        raise ValueError("Report phải chứa pipecat-ai==1.10.0.")
    # docopt only provides an sdist. Keep its build tools in this venv too.
    for name in ("setuptools", "wheel"):
        if any(x["name"] == name for x in entries):
            continue
        data = json.loads(get(f"https://pypi.org/pypi/{name}/json"))
        wheel = next(x for x in data["urls"] if x["filename"].endswith("py3-none-any.whl"))
        entries.append({"name": name, "version": data["info"]["version"], "url": wheel["url"],
                        "sha256": wheel["digests"]["sha256"], "build_only": True})
    wheelhouse = TARGET / "downloads"
    wheelhouse.mkdir(parents=True, exist_ok=True)
    def download(entry):
        filename = Path(urllib.parse.urlparse(entry["url"]).path).name
        path = wheelhouse / filename
        data = path.read_bytes() if path.exists() else get(entry["url"])
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError(f"Package sai checksum: {filename}")
        if not path.exists():
            path.write_bytes(data)
        return {**entry, "filename": filename, "bytes": len(data)}
    with ThreadPoolExecutor(max_workers=6) as pool:
        manifest = list(pool.map(download, entries))
    (TARGET / "download-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Đã xác minh {len(manifest)} archive, "
          f"{sum(x['bytes'] for x in manifest) / 2**20:.2f} MiB.", flush=True)
    if not (TARGET / "bin/python").exists():
        venv.EnvBuilder(with_pip=False).create(TARGET)
    command = [sys.executable, "-m", "pip", "--python", str(TARGET / "bin/python"),
               "--disable-pip-version-check", "--no-cache-dir", "install", "--no-index", "--no-deps"]
    wheels = [str(wheelhouse / x["filename"]) for x in manifest if x["filename"].endswith(".whl")]
    subprocess.run(command + wheels, check=True)
    sources = [str(wheelhouse / x["filename"]) for x in manifest if not x["filename"].endswith(".whl")]
    if sources:
        subprocess.run(command + ["--no-build-isolation"] + sources, check=True)
    subprocess.run([sys.executable, "-m", "pip", "--python", str(TARGET / "bin/python"),
                    "--disable-pip-version-check", "check"], check=True)


if __name__ == "__main__":
    main()
