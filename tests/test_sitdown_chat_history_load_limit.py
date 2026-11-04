"""Bounded chat history reload regression tests."""
import json
import tempfile
import unittest
from pathlib import Path
from sitdown_chat import ChatLog


def message(i, channel=1):
    return {'type': 'chat', 'channel': channel, 'from': '!00000001', 'text': str(i),
            'rx_time': 1792626000, 'mine': False}


class ChatHistoryLoadLimitTests(unittest.TestCase):
    def test_large_saved_channel_retains_latest_messages(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'chat-history.json'
            path.write_text(json.dumps({'version': 1, 'messages': {'1': [message(i) for i in range(1000)]}}))
            rows = ChatLog(limit=5, path=path).messages(1)
            self.assertEqual([row['text'] for row in rows], ['995', '996', '997', '998', '999'])

    def test_non_list_channels_are_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'chat-history.json'
            path.write_text(json.dumps({'version': 1, 'messages': {'bad': 'not rows', '0': [message(1, 0)]}}))
            self.assertEqual([row['text'] for row in ChatLog(path=path).messages(0)], ['1'])


if __name__ == '__main__':
    unittest.main()
