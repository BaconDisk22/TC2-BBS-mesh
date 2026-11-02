"""Offline bounded queue tests; merge event queue PR first."""
import unittest
from sitdown_mqtt_event_queue import MQTTEventQueue


class QueueTests(unittest.TestCase):
    def test_bounded_queue_drops_oldest(self):
        queue = MQTTEventQueue(capacity=2)
        queue.push('centralnj', 'a')
        queue.push('sjmesh', 'b')
        queue.push('centralnj', 'c')
        self.assertEqual(queue.stats(), {'queued': 2, 'dropped': 1})
        self.assertEqual(queue.drain(), [('sjmesh', 'b'), ('centralnj', 'c')])

    def test_invalid_network_is_not_enqueued(self):
        queue = MQTTEventQueue()
        self.assertFalse(queue.push('unknown', 'x'))
        self.assertEqual(queue.drain(), [])

    def test_partial_drain(self):
        queue = MQTTEventQueue()
        for i in range(4):
            queue.push('centralnj', i)
        self.assertEqual(len(queue.drain(limit=2)), 2)
        self.assertEqual(queue.stats()['queued'], 2)


if __name__ == '__main__':
    unittest.main()
