"""Offline MQTT envelope processing; never publishes or forwards.

Packets are decrypted only for channels the operator gave a key for. Text on
those channels is passed on for CHAT; everything else stays metadata only.
"""
import dataclasses
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from sitdown_mqtt_crypto import channel_hash, open_packet
from sitdown_mqtt_dedupe import PacketDeduplicator
from sitdown_mqtt_envelope import decode_service_envelope

BROADCAST = 0xFFFFFFFF
TEXT_PORT = 1
NODEINFO_PORT = 4
MAX_TEXT = 500
MAX_NAMES = 2000


@dataclass(frozen=True)
class ProcessingResult:
    accepted: bool
    reason: str
    network: str
    summary: object = None
    text: str = None
    short: str = ''
    long: str = ''


class MQTTMessagePipeline:
    def __init__(self, deduplicator=None, keys=None):
        self.deduplicator = deduplicator or PacketDeduplicator()
        # {network: {channel name: key bytes}}; empty means metadata only.
        self.keys = {network: dict(found) for network, found in (keys or {}).items()}
        self._names = OrderedDict()
        self._names_lock = Lock()

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
        data = self.open(network, summary)
        if data is None:
            return ProcessingResult(True, 'Encrypted metadata only' if summary.encrypted else 'Decoded metadata only',
                                    network, summary)
        summary = dataclasses.replace(summary, decrypted=summary.encrypted, portnum=int(data.portnum))
        if data.portnum == NODEINFO_PORT:
            self.remember_names(network, summary.from_id, data.payload)
        if data.portnum != TEXT_PORT or summary.to_id != BROADCAST:
            return ProcessingResult(True, 'Opened with channel key', network, summary)
        text = bytes(data.payload).decode('utf-8', errors='replace')[:MAX_TEXT]
        short, long = self.names(network, summary.from_id)
        return ProcessingResult(True, 'Channel text', network, summary, text=text, short=short, long=long)

    def open(self, network, summary):
        """The packet's Data when the operator has this channel's key, else None."""
        key = self.keys.get(network, {}).get(summary.channel_id)
        if key is None:
            return None
        if not summary.encrypted:
            from meshtastic.protobuf import mesh_pb2
            data = mesh_pb2.Data()
            try:
                data.ParseFromString(summary.plain)
            except Exception:
                return None
            return data
        if summary.channel != channel_hash(summary.channel_id, key):
            return None             # a different key on the same channel name, or a direct message
        return open_packet(key, summary.packet_id, summary.from_id, summary.ciphertext)

    def remember_names(self, network, number, payload):
        from meshtastic.protobuf import mesh_pb2
        user = mesh_pb2.User()
        try:
            user.ParseFromString(bytes(payload))
        except Exception:
            return
        with self._names_lock:
            self._names[(network, number)] = (user.short_name[:8], user.long_name[:40])
            self._names.move_to_end((network, number))
            while len(self._names) > MAX_NAMES:
                self._names.popitem(last=False)

    def names(self, network, number):
        with self._names_lock:
            return self._names.get((network, number), ('', ''))
