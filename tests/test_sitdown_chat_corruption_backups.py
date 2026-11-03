"""Corrupted chat history backups must not overwrite earlier recovery evidence."""
import tempfile
import unittest
from pathlib import Path
from sitdown_chat import ChatLog


class ChatCorruptHistoryPreservationTests(unittest.TestCase):
    def test_repeated_corruption_preserves_each_bad_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'chat-history.json'
            path.write_text('{first invalid', encoding='utf-8')
            with self.assertLogs(level='WARNING'):
                ChatLog(path=path)
            self.assertEqual((Path(folder) / 'chat-history.json.bad').read_text(), '{first invalid')
            path.write_text('{second invalid', encoding='utf-8')
            with self.assertLogs(level='WARNING'):
                ChatLog(path=path)
            self.assertEqual((Path(folder) / 'chat-history.json.bad').read_text(), '{first invalid')
            self.assertEqual((Path(folder) / 'chat-history.json.bad.1').read_text(), '{second invalid')

    def test_existing_numbered_backup_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'chat-history.json'
            (Path(folder) / 'chat-history.json.bad').write_text('first')
            (Path(folder) / 'chat-history.json.bad.1').write_text('second')
            path.write_text('broken')
            with self.assertLogs(level='WARNING'):
                ChatLog(path=path)
            self.assertEqual((Path(folder) / 'chat-history.json.bad.2').read_text(), 'broken')


if __name__ == '__main__':
    unittest.main()
