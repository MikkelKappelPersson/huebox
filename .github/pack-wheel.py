#!/usr/bin/env python3
"""Pack the prebuilt huebox binary as a platform wheel (stdlib only).

Layout inside the wheel:
  huebox_bin/__init__.py   console-script launcher, execs the bundled binary
  huebox_bin/huebox        the prebuilt binary (0755)
  huebox-<ver>.dist-info/{METADATA,WHEEL,entry_points.txt,RECORD}
"""
import argparse
import base64
import csv
import hashlib
import io
import os
import zipfile

LAUNCHER = """\
import os
import sys


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    binary = os.path.join(here, "huebox")
    try:
        # installers do not restore the zip's exec bit; fix it ourselves
        os.chmod(binary, 0o755)
    except OSError:
        pass
    os.execv(binary, [binary, *sys.argv[1:]])
"""

METADATA = """\
Metadata-Version: 2.1
Name: huebox
Version: {version}
Summary: Terminal theme editor with live preview
License: MIT
Requires-Python: >=3.8
"""

WHEEL = """\
Wheel-Version: 1.0
Generator: huebox-pack-wheel
Root-Is-Purelib: false
Tag: py3-none-{plat}
"""

ENTRY_POINTS = """\
[console_scripts]
huebox = huebox_bin:main
"""


def _b64digest(data):
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--binary", required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--plat", required=True, help="e.g. linux_x86_64")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    with open(args.binary, "rb") as fh:
        blob = fh.read()
    dist_info = "huebox-%s.dist-info" % args.version
    wheel_name = "huebox-%s-py3-none-%s.whl" % (args.version, args.plat)
    files = {
        "huebox_bin/__init__.py": (LAUNCHER.encode(), 0o644),
        "huebox_bin/huebox": (blob, 0o755),
        "%s/METADATA" % dist_info: (METADATA.format(version=args.version).encode(), 0o644),
        "%s/WHEEL" % dist_info: (WHEEL.format(plat=args.plat).encode(), 0o644),
        "%s/entry_points.txt" % dist_info: (ENTRY_POINTS.encode(), 0o644),
    }
    records = []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for arc, (data, mode) in files.items():
            info = zipfile.ZipInfo(arc)
            info.create_system = 3
            info.external_attr = mode << 16
            zf.writestr(info, data)
            records.append((arc, "sha256=%s" % _b64digest(data), str(len(data))))
        record_name = "%s/RECORD" % dist_info
        out = io.StringIO()
        w = csv.writer(out, lineterminator="\n")
        for arc, digest, size in records:
            w.writerow([arc, digest, size])
        w.writerow([record_name, "", ""])
        zf.writestr(record_name, out.getvalue().encode())
    os.makedirs(args.out, exist_ok=True)
    dest = os.path.join(args.out, wheel_name)
    with open(dest, "wb") as fh:
        fh.write(buf.getvalue())
    print(dest)


if __name__ == "__main__":
    main()
