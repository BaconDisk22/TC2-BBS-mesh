"""Restore a verified Sitdown backup over the live files, keeping a safety copy first.

Only files the app itself writes are restored; anything else in an archive is
reported and skipped. Each file is extracted beside its target and swapped in
with os.replace, so a failure never leaves a half-written file. The BBS server
must be stopped (the desktop enforces this) because it holds the databases open.
"""
import os
import tempfile
import time
import zipfile
from pathlib import Path

from sitdown_backup import create_backup_archive
from sitdown_restore_preflight import inspect_backup_archive

RESTORABLE = ('launcher.json', 'config.ini', 'chat-history.json', 'bulletins.db', 'sitdown-broadcasts.db',
              'node-directory.db')
PRIVATE = ('config.ini', 'node-directory.db', 'chat-history.json')


def restore_plan(archive_path, targets):
    """(files to restore, files skipped) for an archive, after full validation; changes nothing."""
    report = inspect_backup_archive(archive_path)
    restore = [name for name in report['files'] if name in targets]
    skipped = [name for name in report['files'] if name not in targets]
    if not restore:
        raise ValueError('This backup has none of The Sitdown BBS files to restore.')
    return restore, skipped


def restore_backup_archive(archive_path, targets, safety_dir, clock=time.time):
    """Restore known files from archive_path into targets {name: Path}.

    Saves the current versions of those files to a timestamped safety archive in
    safety_dir first. Returns {'restored': [...], 'skipped': [...], 'safety': Path or None}.
    """
    restore, skipped = restore_plan(archive_path, targets)
    existing = [Path(targets[name]) for name in restore if Path(targets[name]).is_file()]
    safety = None
    if existing:
        stamp = time.strftime('%Y%m%d-%H%M%S', time.localtime(clock()))
        safety = Path(safety_dir) / f'before-restore-{stamp}.zip'
        create_backup_archive(safety, [p for p in existing if p.suffix != '.db'],
                              [p for p in existing if p.suffix == '.db'])
    with zipfile.ZipFile(archive_path) as archive:
        for name in restore:
            target = Path(targets[name])
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, temp = tempfile.mkstemp(prefix=f'.{name}.', suffix='.restore', dir=target.parent)
            try:
                with os.fdopen(fd, 'wb') as output, archive.open(name) as source:
                    while True:
                        block = source.read(1024 * 1024)
                        if not block:
                            break
                        output.write(block)
                if name in PRIVATE:
                    os.chmod(temp, 0o600)
                os.replace(temp, target)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
            if target.suffix == '.db':
                # Stale write-ahead files would replay old pages over the restored database.
                for sidecar in (target.with_name(target.name + '-wal'), target.with_name(target.name + '-shm')):
                    if sidecar.exists():
                        sidecar.unlink()
    return {'restored': restore, 'skipped': skipped, 'safety': safety}
