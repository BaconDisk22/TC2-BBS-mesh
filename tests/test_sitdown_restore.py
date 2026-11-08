"""Restoring a backup: validated first, safety copy kept, only known files, atomic swaps."""
import json
import os
import sqlite3
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

import sitdown_desktop as desktop
from sitdown_backup import create_backup_archive
from sitdown_restore import restore_backup_archive, restore_plan


def make_db(path, value):
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE t (v TEXT)')
        db.execute('INSERT INTO t VALUES (?)', (value,))


def read_db(path):
    with sqlite3.connect(path) as db:
        return db.execute('SELECT v FROM t').fetchone()[0]


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.old, self.live, self.safety = root / 'old', root / 'live', root / 'safety'
        self.old.mkdir(), self.live.mkdir()
        (self.old / 'config.ini').write_text('[startup]\nmessage = from backup\n')
        (self.old / 'chat-history.json').write_text(json.dumps({'version': 1, 'messages': {}}))
        make_db(self.old / 'bulletins.db', 'backup board')
        self.archive = root / 'backup.zip'
        create_backup_archive(self.archive, [self.old / 'config.ini', self.old / 'chat-history.json'],
                              [self.old / 'bulletins.db'])
        (self.live / 'config.ini').write_text('[startup]\nmessage = live\n')
        make_db(self.live / 'bulletins.db', 'live board')
        (self.live / 'bulletins.db-wal').write_bytes(b'stale')
        self.targets = {name: self.live / name for name in ('config.ini', 'chat-history.json', 'bulletins.db')}

    def tearDown(self):
        self.temp.cleanup()

    def test_restores_known_files_and_keeps_a_safety_copy(self):
        result = restore_backup_archive(self.archive, self.targets, self.safety, clock=lambda: 0)
        self.assertEqual(sorted(result['restored']), ['bulletins.db', 'chat-history.json', 'config.ini'])
        self.assertIn('from backup', (self.live / 'config.ini').read_text())
        self.assertEqual(read_db(self.live / 'bulletins.db'), 'backup board')
        self.assertFalse((self.live / 'bulletins.db-wal').exists())
        self.assertEqual(stat.S_IMODE(os.stat(self.live / 'config.ini').st_mode), 0o600)
        with zipfile.ZipFile(result['safety']) as safety:
            self.assertEqual(sorted(safety.namelist()), ['bulletins.db', 'config.ini'])   # what existed before
            self.assertIn(b'message = live', safety.read('config.ini'))
        self.assertEqual([p.name for p in self.live.iterdir() if p.name.endswith('.restore')], [])

    def test_unknown_files_are_skipped_and_reported(self):
        targets = {'config.ini': self.live / 'config.ini'}
        restore, skipped = restore_plan(self.archive, targets)
        self.assertEqual((restore, sorted(skipped)), (['config.ini'], ['bulletins.db', 'chat-history.json']))
        with self.assertRaises(ValueError):
            restore_plan(self.archive, {'launcher.json': self.live / 'launcher.json'})

    def test_bad_archive_changes_nothing(self):
        bad = Path(self.temp.name) / 'bad.zip'
        with zipfile.ZipFile(bad, 'w') as archive:
            archive.writestr('../config.ini', 'evil')
        with self.assertRaises(ValueError):
            restore_backup_archive(bad, self.targets, self.safety)
        self.assertIn('message = live', (self.live / 'config.ini').read_text())
        self.assertFalse(self.safety.exists())


class DesktopTests(unittest.TestCase):
    def test_running_server_must_stop_first(self):
        a = desktop.SitdownApp.__new__(desktop.SitdownApp)
        a.supervisor = Mock(running=True)
        a.root, a.stop = None, Mock()
        with patch.object(desktop.messagebox, 'askyesno', return_value=False), \
             patch.object(desktop.filedialog, 'askopenfilename') as picker:
            a.restore_backup()
        a.stop.assert_not_called()
        picker.assert_not_called()

    def test_failed_check_reports_without_changes(self):
        a = desktop.SitdownApp.__new__(desktop.SitdownApp)
        a.supervisor, a.root = None, None
        a.log, a.show_info = Mock(), Mock()
        a.config_path = Mock(return_value=Path('/nonexistent/config.ini'))
        with tempfile.TemporaryDirectory() as folder:
            bad = Path(folder) / 'bad.zip'
            bad.write_bytes(b'not a zip')
            with patch.object(desktop.filedialog, 'askopenfilename', return_value=str(bad)), \
                 patch.object(desktop.messagebox, 'askyesno') as ask:
                a.restore_backup()
        ask.assert_not_called()
        self.assertEqual(a.show_info.call_args.args[0], 'Backup not restored')


if __name__ == '__main__':
    unittest.main()
