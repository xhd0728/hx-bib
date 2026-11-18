"""Stage the two release formats produced by the current runner."""

import hashlib
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tarfile
import zipfile

root = Path(__file__).resolve().parents[1]
platform = os.environ["PLATFORM"]
target = root / "target" / os.environ["TARGET"] / "release"
out = root / "dist"
out.mkdir(exist_ok=True)
if platform == "windows-x64":
    def subsystem(path):
        data = path.read_bytes()
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        return struct.unpack_from("<H", data, pe + 24 + 68)[0]

    assert subsystem(target / "hx-bib-rs.exe") == 3, "CLI must retain its console entry point"
    assert subsystem(target / "hx-bib-desktop.exe") == 2, "Desktop must use the GUI entry point"
    with zipfile.ZipFile(out / f"hx-bib-cli-{platform}.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(target / "hx-bib-rs.exe", "hx-bib-rs.exe")
        archive.write(root.parent / "LICENSE", "LICENSE")
    bundles = list((target / "bundle" / "nsis").glob("*.exe"))
else:
    libraries = subprocess.check_output(["otool", "-L", target / "hx-bib-desktop"], text=True)
    assert "WebKit.framework" in libraries, "Desktop must link the native WebKit UI"
    with tarfile.open(out / f"hx-bib-cli-{platform}.tar.gz", "w:gz") as archive:
        archive.add(target / "hx-bib-rs", arcname="hx-bib-rs")
        archive.add(root.parent / "LICENSE", arcname="LICENSE")
    bundles = list((target / "bundle" / "dmg").glob("*.dmg"))
if not bundles:
    raise SystemExit("No desktop installer found")
for bundle in bundles:
    shutil.copyfile(bundle, out / f"hx-bib-desktop-{platform}{bundle.suffix}")
assets = sorted(out.iterdir())
(out / f"SHA256SUMS-{platform}.txt").write_text(
    "".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in assets), encoding="utf-8"
)
