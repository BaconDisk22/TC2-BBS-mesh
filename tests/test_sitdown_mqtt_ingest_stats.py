"""Offline per-network ingest diagnostics: rejects, duplicates, queue overflow."""
import configparser
import unittest
from meshtastic.protobuf import mqtt_pb2
from sitdown_dual_mqtt import DualMQTTMonitors
from sitdown_mqtt_event_queue import MQTTEventQueue
from sitdown_mqtt_feed import format_stats


def envelope(packet_id=7, from_id=123):
    env = mqtt_pb2.ServiceEnvelope()
    env.channel_id = 'LongFast'
    env.packet.id = packet_id
    setattr(env.packet, 'from', from_id)
    env.packet.encrypted = b'ciphertext'
    return env.SerializeToString()


class IngestStatsTests(unittest.TestCase):
    def test_outcomes_are_counted_per_network(self):
        manager = DualMQTTMonitors(configparser.ConfigParser())
        manager.receive('centralnj', envelope(1))
        manager.receive('centralnj', envelope(1))
        manager.receive('centralnj', b'junk')
        manager.receive('centralnj', envelope(packet_id=0))
        manager.receive('sjmesh', envelope(1))
        stats = manager.ingest_stats()
        self.assertEqual(stats['centralnj'], {
            'accepted': 1, 'duplicates': 1, 'queue_dropped': 0,
            'rejected': {'Invalid MQTT envelope': 1, 'Missing packet identity': 1}})
        self.assertEqual(stats['sjmesh'], {'accepted': 1, 'duplicates': 0, 'rejected': {}, 'queue_dropped': 0})

    def test_queue_overflow_is_attributed_to_the_network_that_lost_events(self):
        manager = DualMQTTMonitors(configparser.ConfigParser(), events=MQTTEventQueue(capacity=2))
        manager.receive('sjmesh', envelope(1))
        manager.receive('sjmesh', envelope(2))
        manager.receive('centralnj', envelope(3))
        manager.receive('centralnj', envelope(4))
        stats = manager.ingest_stats()
        self.assertEqual(stats['sjmesh']['queue_dropped'], 2)
        self.assertEqual(stats['centralnj']['queue_dropped'], 0)
        self.assertEqual(manager.events.stats()['dropped'], 2)

    def test_unknown_network_is_not_counted(self):
        manager = DualMQTTMonitors(configparser.ConfigParser())
        manager.receive('other', envelope())
        self.assertEqual(manager.ingest_stats()['centralnj']['accepted'], 0)
        self.assertNotIn('other', manager.ingest_stats())

    def test_stats_snapshot_is_a_copy(self):
        manager = DualMQTTMonitors(configparser.ConfigParser())
        manager.receive('centralnj', b'junk')
        manager.ingest_stats()['centralnj']['rejected'].clear()
        self.assertEqual(manager.ingest_stats()['centralnj']['rejected'], {'Invalid MQTT envelope': 1})

    def test_format_stats(self):
        line = format_stats({'accepted': 4, 'duplicates': 2, 'queue_dropped': 1,
                             'rejected': {'Invalid MQTT envelope': 3}})
        self.assertEqual(line, 'Accepted 4 · duplicates 2 · rejected 3 (invalid mqtt envelope 3) · queue overflow 1')
        self.assertNotIn('(', format_stats({'accepted': 0, 'duplicates': 0, 'queue_dropped': 0, 'rejected': {}}))


if __name__ == '__main__':
    unittest.main()
