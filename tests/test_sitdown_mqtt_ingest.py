"""Offline broker-callback → pipeline → event queue wiring tests."""
import configparser
import threading
import unittest
from types import SimpleNamespace
from meshtastic.protobuf import mqtt_pb2
from sitdown_dual_mqtt import DualMQTTMonitors
from sitdown_mqtt_event_queue import MQTTEventQueue


def envelope(packet_id=7, channel='LongFast'):
    env = mqtt_pb2.ServiceEnvelope()
    env.gateway_id = '!12345678'
    env.channel_id = channel
    env.packet.id = packet_id
    setattr(env.packet, 'from', 123)
    env.packet.to = 0xFFFFFFFF
    env.packet.encrypted = b'ciphertext'
    return env.SerializeToString()


def message(payload, retain=False):
    return SimpleNamespace(retain=retain, topic='msh/US/2/e/LongFast/!12345678', payload=payload)


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.manager = DualMQTTMonitors(configparser.ConfigParser())

    def test_broker_message_reaches_queue_with_network_label(self):
        self.manager.monitors['centralnj']._message(None, None, message(envelope()))
        events = self.manager.drain_events()
        self.assertEqual(len(events), 1)
        network, result = events[0]
        self.assertEqual((network, result.network), ('centralnj', 'centralnj'))
        self.assertTrue(result.accepted)
        self.assertTrue(result.summary.encrypted)

    def test_networks_stay_separate(self):
        self.manager.monitors['centralnj']._message(None, None, message(envelope(1)))
        self.manager.monitors['sjmesh']._message(None, None, message(envelope(2)))
        self.assertEqual([n for n, _ in self.manager.drain_events()], ['centralnj', 'sjmesh'])

    def test_rejections_are_queued_not_raised(self):
        monitor = self.manager.monitors['sjmesh']
        monitor._message(None, None, message(b'not protobuf'))
        monitor._message(None, None, message(envelope()))
        monitor._message(None, None, message(envelope()))
        reasons = [r.reason for _, r in self.manager.drain_events()]
        self.assertEqual(reasons, ['Invalid MQTT envelope', 'Encrypted metadata only', 'Duplicate packet'])
        self.assertEqual(monitor.snapshot()['total'], 3)

    def test_retained_messages_are_not_processed(self):
        self.manager.monitors['centralnj']._message(None, None, message(envelope(), retain=True))
        self.assertEqual(self.manager.drain_events(), [])

    def test_sink_failure_does_not_break_counter(self):
        manager = DualMQTTMonitors(configparser.ConfigParser())
        manager.monitors['centralnj'].sink = lambda payload: 1 / 0
        with self.assertLogs(level='WARNING'):
            manager.monitors['centralnj']._message(None, None, message(envelope()))
        self.assertEqual(manager.snapshot()['centralnj']['total'], 1)

    def test_queue_overflow_is_bounded(self):
        manager = DualMQTTMonitors(configparser.ConfigParser(), events=MQTTEventQueue(capacity=3))
        for i in range(1, 6):
            manager.monitors['centralnj']._message(None, None, message(envelope(i)))
        self.assertEqual(manager.events.stats(), {'queued': 3, 'dropped': 2})

    def test_concurrent_broker_threads_dedupe_safely(self):
        manager = DualMQTTMonitors(configparser.ConfigParser(), events=MQTTEventQueue(capacity=1000))
        payload = envelope(99)
        def flood(network):
            for _ in range(200):
                manager.monitors[network]._message(None, None, message(payload))
        threads = [threading.Thread(target=flood, args=(n,)) for n in ('centralnj', 'sjmesh') * 2]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        accepted = [n for n, r in manager.drain_events(limit=1000) if r.accepted]
        self.assertEqual(sorted(accepted), ['centralnj', 'sjmesh'])


if __name__ == '__main__':
    unittest.main()
