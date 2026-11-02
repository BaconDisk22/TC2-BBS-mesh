"""Chat history survives restarts, stays bounded, and tolerates a damaged file."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sitdown_desktop as desktop
from sitdown_chat import ChatLog


def event(text, channel=1, mine=False, **extra):
    e = {'type': 'chat', 'channel': channel, 'from': '!02ed8680', 'short': 'XiG4', 'long': 'Rav4 KC1TCD',
         'text': text, 'rx_time': 1792626000, 'mine': mine}
    e.update(extra)
    return e


class ChatHistoryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / 'chat-history.json'

    def tearDown(self):
        self.dir.cleanup()

    def test_messages_survive_a_restart_in_order_per_channel(self):
        log = ChatLog(path=self.path)
        log.add(event('one'))
        log.add(event('two', mine=True))
        log.add(event('primary', channel=0))
        again = ChatLog(path=self.path)
        self.assertEqual([m['text'] for m in again.messages(1)], ['one', 'two'])
        self.assertTrue(again.messages(1)[1]['mine'])
        self.assertEqual([m['text'] for m in again.messages(0)], ['primary'])

    def test_history_stays_bounded_on_disk(self):
        log = ChatLog(limit=3, path=self.path)
        for i in range(10):
            log.add(event(f'm{i}'))
        self.assertEqual([m['text'] for m in ChatLog(limit=3, path=self.path).messages(1)], ['m7', 'm8', 'm9'])
        self.assertEqual(len(json.loads(self.path.read_text())['messages']['1']), 3)

    def test_clear_removes_saved_messages(self):
        log = ChatLog(path=self.path)
        log.add(event('secret'))
        log.clear()
        self.assertEqual(ChatLog(path=self.path).channels(), [])
        self.assertNotIn('secret', self.path.read_text())

    def test_damaged_file_is_set_aside_not_lost(self):
        self.path.write_text('{not json')
        with self.assertLogs(level='WARNING'):
            log = ChatLog(path=self.path)
        self.assertEqual(log.channels(), [])
        self.assertEqual(self.path.with_name('chat-history.json.bad').read_text(), '{not json')

    def test_bad_rows_are_dropped_and_fields_cleaned(self):
        self.path.write_text(json.dumps({'version': 1, 'messages': {'1': [
            event('ok', evil='x'), {'type': 'chat', 'channel': 99, 'text': 'x', 'from': '!1'},
            'junk', event(5), event('long' * 500)]}}))
        rows = ChatLog(path=self.path).messages(1)
        self.assertEqual([r['text'][:4] for r in rows], ['ok', 'long'])
        self.assertNotIn('evil', rows[0])
        self.assertEqual(len(rows[1]['text']), 500)

    def test_unwritable_location_keeps_memory_history(self):
        blocker = Path(self.dir.name) / 'file'
        blocker.write_text('')
        with self.assertLogs(level='WARNING'):
            log = ChatLog(path=blocker / 'chat-history.json')
            log.add(event('still here'))
        self.assertEqual(log.messages(1)[0]['text'], 'still here')

    def test_desktop_history_lives_beside_launcher_config(self):
        with patch.object(desktop, 'CONFIG', Path(self.dir.name) / 'launcher.json'):
            app = desktop.SitdownApp.__new__(desktop.SitdownApp)
            app.chat_log = ChatLog(path=desktop.CONFIG.with_name('chat-history.json'))
        self.assertEqual(app.chat_log.path, self.path)

    def test_clear_button_asks_first(self):
        app = desktop.SitdownApp.__new__(desktop.SitdownApp)
        app.chat_log = ChatLog(path=self.path)
        app.chat_log.add(event('keep'))
        app.chat_views, app.root, app.log = {}, None, lambda *_: None
        with patch.object(desktop.messagebox, 'askyesno', return_value=False):
            app.clear_chat_history()
        self.assertEqual(len(app.chat_log.messages(1)), 1)
        with patch.object(desktop.messagebox, 'askyesno', return_value=True):
            app.clear_chat_history()
        self.assertEqual(app.chat_log.channels(), [])


if __name__ == '__main__':
    unittest.main()
