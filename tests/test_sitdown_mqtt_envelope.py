"""Offline Meshtastic ServiceEnvelope decoder regression tests."""
import unittest
from sitdown_mqtt_envelope import decode_service_envelope


class EnvelopeDecoderTests(unittest.TestCase):
    def test_rejects_empty_and_oversized_payload(self):
        for payload in (b'', b'x' * 65537, None):
            with self.subTest(payload=type(payload).__name__):
                with self.assertRaises(ValueError):
                    decode_service_envelope(payload)

    def test_rejects_packetless_envelope(self):
        from meshtastic.protobuf import mqtt_pb2
        envelope = mqtt_pb2.ServiceEnvelope()
        envelope.gateway_id = '!12345678'
        with self.assertRaises(ValueError):
            decode_service_envelope(envelope.SerializeToString())

    def test_decodes_packet_without_decryption(self):
        from meshtastic.protobuf import mqtt_pb2
        envelope = mqtt_pb2.ServiceEnvelope()
        envelope.gateway_id = '!12345678'
        envelope.channel_id = 'test'
        envelope.packet.id = 42
        setattr(envelope.packet, 'from', 123)
        envelope.packet.to = 456
        envelope.packet.encrypted = b'ciphertext'
        result = decode_service_envelope(envelope.SerializeToString())
        self.assertEqual(result.packet_id, 42)
        self.assertEqual(result.gateway_id, '!12345678')
        self.assertTrue(result.encrypted)
        self.assertIsNone(result.portnum)


if __name__ == '__main__':
    unittest.main()
