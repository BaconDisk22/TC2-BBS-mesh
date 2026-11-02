#!/usr/bin/env python3
"""Stamp a build version into the source and the packaged Mac app.

  stamp_version.py source VERSION         write sitdown_version.py before PyInstaller
  stamp_version.py bundle APP VERSION     set Info.plist versions, then re-sign ad hoc
"""
import plistlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9.+-]{0,40}$')


def check(version):
    if not VALID.match(version):
        raise SystemExit(f'Invalid version: {version!r}')
    return version


def write_source(version, path=ROOT / 'sitdown_version.py'):
    path.write_text('"""Release version of The Sitdown BBS; release builds overwrite this file."""\n'
                    f'VERSION = {check(version)!r}\n')


def write_plist(app, version):
    plist = Path(app) / 'Contents' / 'Info.plist'
    with plist.open('rb') as f:
        info = plistlib.load(f)
    info['CFBundleShortVersionString'] = info['CFBundleVersion'] = check(version)
    with plist.open('wb') as f:
        plistlib.dump(info, f)


def main(argv):
    if len(argv) == 3 and argv[1] == 'source':
        write_source(argv[2])
    elif len(argv) == 4 and argv[1] == 'bundle':
        write_plist(argv[2], argv[3])
        # Editing Info.plist invalidates the ad-hoc signature PyInstaller applied.
        subprocess.run(['codesign', '--force', '--deep', '--sign', '-', argv[2]], check=True)
        subprocess.run(['codesign', '--verify', '--deep', '--strict', argv[2]], check=True)
    else:
        raise SystemExit(__doc__)


if __name__ == '__main__':
    main(sys.argv)
