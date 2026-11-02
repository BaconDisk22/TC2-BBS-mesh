"""Decode Meshtastic MQTT ServiceEnvelope without decrypting or forwarding."""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class EnvelopeSummary:
    gateway_id: str
    channel_id: str
    packet_id: int
    from_id: int
    to_id: int
    encrypted: bool
    portnum: Optional[int]


def decode_service_envelope(payload: bytes) -> EnvelopeSummary:
    """Parse protobuf bytes offline; never publish or attempt decryption."""
    if not isinstance(payload, bytes) or not payload or len(payload) > 65536:
        raise ValueError("Invalid or oversized MQTT envelope")
    from meshtastic.protobuf import mqtt_pb2
    envelope = mqtt_pb2.ServiceEnvelope()
    try:
        envelope.ParseFromString(payload)
    except Exception as exc:
        raise ValueError("Malformed MQTT envelope") from exc
    if not envelope.HasField("packet"):
        raise ValueError("MQTT envelope has no packet")
    packet = envelope.packet
    decoded = packet.HasField("decoded")
    return EnvelopeSummary(
        gateway_id=str(envelope.gateway_id),
        channel_id=str(envelope.channel_id),
        packet_id=int(packet.id),
        from_id=int(getattr(packet, "from")),
        to_id=int(packet.to),
        encrypted=not decoded,
        portnum=int(packet.decoded.portnum) if decoded else None,
    )
