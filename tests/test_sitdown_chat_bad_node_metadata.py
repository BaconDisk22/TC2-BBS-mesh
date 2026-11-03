"""Malformed node metadata must not discard otherwise valid chat packets."""
import unittest
from types import SimpleNamespace
from sitdown_chat import chat_event, BROADCAST

class ChatNodeMetadataTests(unittest.TestCase):
    def test_malformed_node_entries_do_not_crash(self):
        packet = {'decoded': {'portnum': 'TEXT_MESSAGE_APP', 'payload': b'hello'},
                  'to': BROADCAST, 'fromId': '!00000001', 'from': 1, 'channel': 0}
        for nodes in ({'!00000001': None}, {'!00000001': 'invalid'},
                      {'!00000001': {'user': []}}):
            with self.subTest(nodes=nodes):
                interface = SimpleNamespace(nodes=nodes, myInfo=SimpleNamespace(my_node_num=2))
                event = chat_event(packet, interface, clock=lambda: 123)
                self.assertEqual(event['text'], 'hello')
                self.assertEqual(event['short'], '')

if __name__ == '__main__':
    unittest.main()
