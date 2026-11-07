"""Operator direct messages ("conversation mode").

After the operator sends a direct message from the desktop, that node's plain
replies go to the operator's Direct tab instead of the BBS menu for
CONVERSATION_SECONDS. A reply starting with '/' still goes to the BBS, and the
operator can end the conversation at any time. Everyone else uses the BBS
exactly as before.
"""
import re
import threading
import time

from sitdown_chat import BROADCAST, MAX_TEXT

CONVERSATION_SECONDS = 600
MAX_CONVERSATIONS = 32


def peer_id(value):
    """'!xxxxxxxx' for a node id or number, or None."""
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            number = value
        elif re.fullmatch(r'!?[0-9a-fA-F]{8}', str(value).strip()):
            number = int(str(value).strip().lstrip('!'), 16)
        else:
            return None
    except (TypeError, ValueError):
        return None
    if not 0 < number < BROADCAST:
        return None
    return f'!{number:08x}'


class Conversations:
    """Open operator conversations, each expiring CONVERSATION_SECONDS after the last message."""

    def __init__(self, seconds=CONVERSATION_SECONDS, clock=time.monotonic):
        self.seconds, self.clock = seconds, clock
        self._lock = threading.Lock()
        self._expires = {}

    def _prune(self, now):
        for peer in [p for p, until in self._expires.items() if until <= now]:
            del self._expires[peer]

    def open(self, peer):
        with self._lock:
            now = self.clock()
            self._prune(now)
            if peer not in self._expires and len(self._expires) >= MAX_CONVERSATIONS:
                oldest = min(self._expires, key=self._expires.get)
                del self._expires[oldest]
            self._expires[peer] = now + self.seconds

    def close(self, peer):
        with self._lock:
            return self._expires.pop(peer, None) is not None

    def claims(self, peer, text):
        """True when this reply belongs to the operator rather than the BBS."""
        if not isinstance(text, str) or text.lstrip().startswith('/'):
            return False
        with self._lock:
            now = self.clock()
            self._prune(now)
            if peer not in self._expires:
                return False
            self._expires[peer] = now + self.seconds
            return True

    def active(self):
        with self._lock:
            self._prune(self.clock())
            return sorted(self._expires)


def dm_event(packet, interface, clock=time.time):
    """A desktop event for a text message addressed to this radio, or None."""
    try:
        decoded = packet['decoded']
        if decoded.get('portnum') != 'TEXT_MESSAGE_APP':
            return None
        mine = getattr(getattr(interface, 'myInfo', None), 'my_node_num', None)
        if not isinstance(mine, int) or packet.get('to') != mine or packet.get('from') == mine:
            return None
        text = bytes(decoded['payload']).decode('utf-8', errors='replace')[:MAX_TEXT]
        peer = peer_id(packet.get('fromId')) or peer_id(packet['from'])
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    if peer is None:
        return None
    nodes = getattr(interface, 'nodes', None) or {}
    node = nodes.get(peer) if isinstance(nodes, dict) else None
    user = node.get('user') if isinstance(node, dict) else None
    user = user if isinstance(user, dict) else {}
    return {'type': 'dm', 'peer': peer, 'from': peer,
            'short': str(user.get('shortName', '')), 'long': str(user.get('longName', '')),
            'text': text, 'rx_time': int(packet.get('rxTime') or clock()), 'mine': False}


def claim_reply(packet, interface, conversations):
    """The operator's conversation event when this packet is a reply for the operator, else None (BBS handles it)."""
    direct = dm_event(packet, interface)
    if direct is not None and conversations.claims(direct['peer'], direct['text']):
        return direct
    return None


def dm_sent_event(peer, text, sender, packet_id=0, clock=time.time):
    return {'type': 'dm', 'peer': peer, 'from': sender, 'short': '', 'long': '',
            'text': text, 'rx_time': int(clock()), 'mine': True,
            'id': packet_id, 'status': 'sending' if packet_id else 'unconfirmed'}


def dm_status(packet, peer):
    """('delivered' | 'relayed' | 'failed', reason) from the routing reply to a direct message.

    An ACK from the destination itself means delivered; an implicit ACK (a
    neighbour rebroadcasting it) only means it left on its way.
    """
    routing = ((packet or {}).get('decoded') or {}).get('routing') or {}
    reason = str(routing.get('errorReason', 'NONE'))
    if reason != 'NONE':
        return 'failed', reason
    origin = peer_id((packet or {}).get('fromId')) or peer_id((packet or {}).get('from'))
    return ('delivered' if origin == peer else 'relayed'), reason
