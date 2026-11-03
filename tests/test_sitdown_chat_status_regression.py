"""Regression coverage for chat relay status transitions."""
import unittest
from sitdown_chat import ChatLog, sent_event


class ChatStatusTransitionTests(unittest.TestCase):
    def test_final_relay_result_cannot_be_overwritten(self):
        log = ChatLog()
        log.add(sent_event(0, 'hello', '!12345678', packet_id=17))
        self.assertIsNotNone(log.set_status(17, 'relayed'))
        self.assertIsNone(log.set_status(17, 'unconfirmed'))
        self.assertIsNone(log.set_status(17, 'failed', 'late callback'))
        self.assertEqual(log.messages(0)[0]['status'], 'relayed')

    def test_failed_result_remains_final(self):
        log = ChatLog()
        log.add(sent_event(0, 'hello', '!12345678', packet_id=18))
        log.set_status(18, 'failed', 'NO_ROUTE')
        self.assertIsNone(log.set_status(18, 'relayed'))
        self.assertEqual(log.messages(0)[0]['reason'], 'NO_ROUTE')

    def test_sending_cannot_reset_unconfirmed(self):
        log = ChatLog()
        log.add(sent_event(0, 'hello', '!12345678', packet_id=19))
        log.set_status(19, 'unconfirmed')
        self.assertIsNone(log.set_status(19, 'sending'))
        self.assertEqual(log.messages(0)[0]['status'], 'unconfirmed')

    def test_unknown_packet_does_not_modify_history(self):
        log = ChatLog()
        log.add(sent_event(0, 'hello', '!12345678', packet_id=20))
        self.assertIsNone(log.set_status(21, 'relayed'))
        self.assertEqual(log.messages(0)[0]['status'], 'sending')


if __name__ == '__main__':
    unittest.main()
