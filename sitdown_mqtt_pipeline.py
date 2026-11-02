"""Offline MQTT envelope processing; never decrypts, publishes or forwards."""
from dataclasses import dataclass
from sitdown_mqtt_dedupe import PacketDeduplicator
from sitdown_mqtt_envelope import decode_service_envelope


@dataclass(frozen=True)
class ProcessingResult:
    accepted: bool
    reason: str
    network: str
    summary: object = None


class MQTTMessagePipeline:
    def __init__(self, deduplicator=None):
        self.deduplicator = deduplicator or PacketDeduplicator()

    def process(self, network, payload):
        if network not in ('centralnj', 'sjmesh'):
            return ProcessingResult(False, 'Unknown network', network)
        try:
            summary = decode_service_envelope(payload)
        except (ValueError, TypeError):
            return ProcessingResult(False, 'Invalid MQTT envelope', network)
        if not summary.channel_id or not summary.from_id or not summary.packet_id:
            return ProcessingResult(False, 'Missing packet identity', network)
        if self.deduplicator.duplicate(
            network, summary.channel_id, summary.from_id, summary.packet_id
        ):
            return ProcessingResult(False, 'Duplicate packet', network)
        return ProcessingResult(True, 'Encrypted metadata only' if summary.encrypted else 'Decoded metadata only', network, summary)
