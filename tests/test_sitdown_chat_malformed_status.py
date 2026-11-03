"""Malformed persisted chat status must not crash reload."""
import json
import tempfile
import unittest
from pathlib import Path
from sitdown_chat import ChatLog, clean_event


def sample(status):
    return {'type': 'chat', 'channel': 0, 'from': '!00000001', 'text': 'hello',
            'rx_time': 1792626000, 'mine': True, 'status': status}


class MalformedStatusTests(unittest.TestCase):
    def test_unhashable_status_is_unconfirmed(self):
        for value in ([], {}, ['relayed']):
            with self.subTest(value=value):
                self.assertEqual(clean_event(sample(value))['status'], 'unconfirmed')

    def test_history_reload_tolerates_invalid_status(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'chat-history.json'
            path.write_text(json.dumps({'version': 1, 'messages': {'0': [sample([]), sample('relayed')]}}))
            self.assertEqual([e['status'] for e in ChatLog(path=path).messages(0)],
                             ['unconfirmed', 'relayed'])


if __name__ == '__main__':
    unittest.main()
