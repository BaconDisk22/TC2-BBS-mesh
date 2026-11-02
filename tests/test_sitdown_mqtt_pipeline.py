"""Offline pipeline tests; merge packet pipeline PR first."""
import unittest
from meshtastic.protobuf import mqtt_pb2
from sitdown_mqtt_pipeline import MQTTMessagePipeline


def sample(packet_id=7):
    envelope = mqtt_pb2.ServiceEnvelope()
    envelope.gateway_id = '!12345678'
    envelope.channel_id = 'test'
    envelope.packet.id = packet_id
    setattr(envelope.packet, 'from', 123)
    envelope.packet.to = 456
    envelope.packet.encrypted = b'ciphertext'
    return envelope.SerializeToString()


class PipelineTests(unittest.TestCase):
    def test_rejects_invalid_network_and_payload(self):
        pipeline = MQTTMessagePipeline()
        self.assertFalse(pipeline.process('unknown', sample()).accepted)
        self.assertFalse(pipeline.process('centralnj', b'').accepted)

    def test_dedupe_isolated_per_network(self):
        pipeline = MQTTMessagePipeline()
        packet = sample()
        self.assertTrue(pipeline.process('centralnj', packet).accepted)
        self.assertFalse(pipeline.process('centralnj', packet).accepted)
        self.assertTrue(pipeline.process('sjmesh', packet).accepted)

    def test_encrypted_payload_is_not_decrypted(self):
        result = MQTTMessagePipeline().process('centralnj', sample())
        self.assertTrue(result.accepted)
        self.assertTrue(result.summary.encrypted)
        self.assertIsNone(result.summary.portnum)


if __name__ == '__main__':
    unittest.main()
