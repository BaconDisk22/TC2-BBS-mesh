"""Operator-initiated channel text to an MQTT broker; the only MQTT publishing path.

Every send needs: the network enabled with allow_publish on (off by default),
the operator's confirmation this session, a channel key for the target
channel, a subscribed msh/<region>/2/e/<channel>/+ topic for that channel, and
room in the per-network rate limit. Nothing here bridges or forwards packets;
it only publishes text the operator typed.
"""
import secrets

from sitdown_chat import ChatSendLimiter
from sitdown_dm import peer_id
from sitdown_mqtt_crypto import channel_hash, crypt, keys_for_topics
from sitdown_mqtt_send_policy import check_send_permission
from sitdown_online import topic_list

BROADCAST = 0xFFFFFFFF
TEXT_PORT = 1
HOP_LIMIT = 3
MAX_BYTES = 200


def publish_topic(topics, room, gateway):
    """The topic to publish on: the subscribed '+' topic for this channel, with our gateway id."""
    for topic in topic_list(topics):
        parts = topic.split('/')
        if parts[-1] == '+' and len(parts) >= 6 and parts[-4:-2] == ['2', 'e'] and parts[-2] == room:
            return '/'.join(parts[:-1] + [gateway])
    return None


def send_rooms(topics, keys):
    """Channels this profile could send to: subscribed with a '+' topic and keyed."""
    rooms = []
    for topic in topic_list(topics):
        parts = topic.split('/')
        if parts[-1] == '+' and len(parts) >= 6 and parts[-4:-2] == ['2', 'e'] and parts[-2] in keys:
            rooms.append(parts[-2])
    return rooms


def build_envelope(text, room, key, sender, packet_id):
    """Serialized ServiceEnvelope carrying one encrypted broadcast text packet."""
    from meshtastic.protobuf import mesh_pb2, mqtt_pb2
    data = mesh_pb2.Data()
    data.portnum = TEXT_PORT
    data.payload = text.encode('utf-8')
    envelope = mqtt_pb2.ServiceEnvelope()
    envelope.channel_id = room
    envelope.gateway_id = f'!{sender:08x}'
    packet = envelope.packet
    setattr(packet, 'from', sender)
    packet.to = BROADCAST
    packet.id = packet_id
    packet.channel = channel_hash(room, key)
    packet.hop_limit = HOP_LIMIT
    packet.hop_start = HOP_LIMIT
    packet.encrypted = crypt(key, packet_id, sender, data.SerializeToString())
    return envelope.SerializeToString()


class MQTTSender:
    """Checks every gate, then publishes on an already-connected monitor client."""

    def __init__(self, limiters=None, new_id=None):
        self.limiters = limiters or {}
        self.new_id = new_id or (lambda: secrets.randbelow(0xFFFFFFFE) + 1)
        self.receipts = {}              # packet id -> paho publish info, until the broker's PUBACK

    def accepted(self):
        """Packet ids the broker has acknowledged (PUBACK) since the last call."""
        done = []
        for packet_id, info in list(self.receipts.items()):
            check = getattr(info, 'is_published', None)
            try:
                published = bool(check()) if callable(check) else False
            except Exception:
                published = False
            if published:
                done.append(packet_id)
                del self.receipts[packet_id]
        return done

    def send(self, config, network, room, text, *, client, sender, demo=False, session_confirmed=False):
        """Return the packet id published, or raise ValueError saying which gate stopped it."""
        decision = check_send_permission(config, network, demo=demo, session_confirmed=session_confirmed)
        if not decision.allowed:
            raise ValueError(decision.reason + '.')
        if not isinstance(text, str) or not text.strip() or len(text.encode('utf-8')) > MAX_BYTES:
            raise ValueError(f'Message must contain 1–{MAX_BYTES} UTF-8 bytes.')
        section = 'mqtt_' + network
        key = keys_for_topics(config.get(section, 'channel_keys', fallback=''),
                              config.get(section, 'topic', fallback='')).get(room)
        if key is None:
            raise ValueError(f'No valid key for {room}; add it under Channel keys in Settings.')
        number = peer_id(config.get(section, 'send_as', fallback='').strip() or sender)
        if number is None:
            raise ValueError('Unknown sending node: start the BBS server once, or fill in "Send as node id".')
        number = int(number[1:], 16)
        topic = publish_topic(config.get(section, 'topic', fallback=''), room, f'!{number:08x}')
        if topic is None:
            raise ValueError(f'Subscribe to msh/<region>/2/e/{room}/+ on this network to send there.')
        if client is None or not client.is_connected():
            raise ValueError('This network is not connected to its broker.')
        limiter = self.limiters.setdefault(network, ChatSendLimiter())
        if not limiter.reserve():
            raise ValueError('Slow down: one message every 2 seconds and 6 per minute.')
        packet_id = self.new_id()
        info = client.publish(topic, build_envelope(text, room, key, number, packet_id), qos=1)
        if getattr(info, 'rc', 0) != 0:
            raise ValueError('The broker connection refused the message; try again.')
        self.receipts[packet_id] = info
        while len(self.receipts) > 100:
            self.receipts.pop(next(iter(self.receipts)))
        return packet_id, f'!{number:08x}'
