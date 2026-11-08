"""/node over the radio also finds nodes the radio forgot, from the desktop's saved list (read-only)."""
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from sitdown_nodedir import NodeDirectory, directory_path
from sitdown_utilities import LocalUtilities


def service(nodes, path):
    svc = LocalUtilities.__new__(LocalUtilities)
    svc.lock = threading.RLock()
    svc.interface = SimpleNamespace(nodes=nodes)
    svc.node_directory_path = path
    return svc


RADIO = {'!00000001': {'user': {'id': '!00000001', 'shortName': 'R1', 'longName': 'Radio One'}, 'num': 1}}


class NodeCommandTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / 'node-directory.db'
        saved = NodeDirectory(self.path, clock=lambda: time.time() - 7200)
        saved.record_mqtt('centralnj', 0x0000abcd, 'GONE', 'Forgotten Node')
        saved.record_mqtt('centralnj', 1, 'R1', 'Radio One')
        saved.close()

    def tearDown(self):
        self.folder.cleanup()

    def test_forgotten_node_found_in_saved_list(self):
        reply = service(RADIO, self.path).command('/node forgotten', '!b2c649cb')
        self.assertEqual(reply, 'GONE (Forgotten Node) !0000abcd - last heard 2 hours ago via CentralNJ (saved list)')

    def test_radio_match_comes_first_without_duplicates(self):
        reply = service(RADIO, self.path).command('/node R1', '!b2c649cb')
        self.assertIn('!00000001 R1 / Radio One', reply)
        self.assertNotIn('(saved list)', reply)

    def test_no_saved_list_or_no_match(self):
        self.assertEqual(service({}, None).command('/node nobody', 'x'),
                         'No matching nodes in the radio database or the saved node list.')
        self.assertEqual(service({}, self.path).command('/node nobody', 'x'),
                         'No matching nodes in the radio database or the saved node list.')

    def test_reading_never_creates_or_changes_the_file(self):
        missing = Path(self.folder.name) / 'none.db'
        service({}, missing).command('/node x', 'x')
        self.assertFalse(missing.exists())
        before = self.path.stat().st_mtime_ns
        service({}, self.path).command('/node gone', 'x')
        self.assertEqual(self.path.stat().st_mtime_ns, before)

    def test_directory_path_prefers_the_file_beside_config(self):
        config = Path(self.folder.name) / 'config.ini'
        self.assertEqual(directory_path(config), self.path.resolve())
        self.assertEqual(directory_path(Path(self.folder.name) / 'sub' / 'config.ini'),
                         Path.home() / '.sitdown-bbs' / 'node-directory.db')


if __name__ == '__main__':
    unittest.main()
