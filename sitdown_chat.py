"""Radio channel chat for the desktop.

Channel (broadcast) text messages heard by the BBS radio are passed to the
desktop for display. Direct messages stay with the BBS command handler unless
the operator has a conversation open with that node (see sitdown_dm).
Sending is operator-initiated only, gated on the desktop and rate limited here.
"""
import json
import logging
import os
import re
import time
from collections import deque
from pathlib import Path

BROADCAST = 0xFFFFFFFF
MAX_TEXT = 500
MAX_CHANNELS = 8
STATUSES = ('sending', 'relayed', 'delivered', 'failed', 'unconfirmed')
FINAL = ('relayed', 'delivered', 'failed')
PEER = re.compile(r'![0-9a-f]{8}')
CONFIRM_TIMEOUT = 120


def chat_event(packet, interface, clock=time.time):
    """Return a desktop chat event for a channel text message, or None."""
    try:
        decoded = packet['decoded']
        if decoded.get('portnum') != 'TEXT_MESSAGE_APP' or packet.get('to') != BROADCAST:
            return None
        text = bytes(decoded['payload']).decode('utf-8', errors='replace')[:MAX_TEXT]
        channel = int(packet.get('channel', 0))
        sender = packet.get('fromId') or f"!{int(packet['from']):08x}"
    except (KeyError, TypeError, ValueError):
        return None
    if not 0 <= channel < MAX_CHANNELS:
        return None
    nodes = getattr(interface, 'nodes', None) or {}
    node = nodes.get(sender) if isinstance(nodes, dict) else None
    user = node.get('user') if isinstance(node, dict) else None
    user = user if isinstance(user, dict) else {}
    mine = packet.get('from') == getattr(getattr(interface, 'myInfo', None), 'my_node_num', None)
    return {'type': 'chat', 'channel': channel, 'from': sender,
            'short': str(user.get('shortName', '')), 'long': str(user.get('longName', '')),
            'text': text, 'rx_time': int(packet.get('rxTime') or clock()), 'mine': mine}


class ChatSendLimiter:
    """At least min_gap seconds between sends and at most per_minute in any minute."""

    def __init__(self, min_gap=2.0, per_minute=6, clock=time.monotonic):
        self.min_gap, self.per_minute, self.clock = min_gap, per_minute, clock
        self._sent = deque()

    def reserve(self):
        now = self.clock()
        while self._sent and now - self._sent[0] >= 60:
            self._sent.popleft()
        if self._sent and now - self._sent[-1] < self.min_gap:
            return False
        if len(self._sent) >= self.per_minute:
            return False
        self._sent.append(now)
        return True


def sent_event(channel, text, sender, packet_id=0, clock=time.time):
    """The operator's own message, echoed into the chat log once the radio accepted it."""
    return {'type': 'chat', 'channel': channel, 'from': sender, 'short': '', 'long': '',
            'text': text, 'rx_time': int(clock()), 'mine': True,
            'id': packet_id, 'status': 'sending' if packet_id else 'unconfirmed'}


def routing_status(packet):
    """('relayed' | 'failed', reason) from the radio's routing reply to a sent message."""
    routing = ((packet or {}).get('decoded') or {}).get('routing') or {}
    reason = str(routing.get('errorReason', 'NONE'))
    return ('relayed' if reason == 'NONE' else 'failed'), reason


def status_label(event, now=None):
    status = event.get('status')
    if status == 'sending' and (now if now is not None else time.time()) - event['rx_time'] > CONFIRM_TIMEOUT:
        status = 'unconfirmed'
    return {'sending': '… sending', 'relayed': '✓ relayed', 'delivered': '✓✓ delivered',
            'unconfirmed': '? no confirmation',
            'failed': f"✗ not relayed ({event.get('reason') or 'no reply'})"}.get(status, '')


def format_message(event):
    stamp = time.strftime('%Y-%m-%d %H:%M', time.localtime(event['rx_time']))
    who = ' '.join(part for part in (event['short'], f"({event['long']})" if event['long'] else '') if part)
    if event['mine']:
        label = status_label(event)
        who = 'You' + (f'  {label}' if label else '')
    else:
        who = (who + ' ' if who else '') + event['from']
    return f"{stamp}  {who}\n    {event['text']}"


def event_key(event):
    """Where a message lives: a channel index, or 'dm:!xxxxxxxx' for a direct conversation."""
    return f"dm:{event['peer']}" if event.get('type') == 'dm' else event['channel']


def dm_peer(key):
    return key[3:] if isinstance(key, str) and key.startswith('dm:') else None


def clean_event(event):
    """A chat or direct-message event reduced to known fields with checked types, or None."""
    try:
        kind = event.get('type')
        if kind == 'dm':
            place = {'peer': event['peer']}
            valid = isinstance(event['peer'], str) and PEER.fullmatch(event['peer']) is not None
        else:
            channel = int(event['channel'])
            place = {'channel': channel}
            valid = kind == 'chat' and 0 <= channel < MAX_CHANNELS
        valid = valid and isinstance(event.get('text'), str) and isinstance(event.get('from'), str)
        rx_time = int(event.get('rx_time') or 0)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    if not valid:
        return None
    clean = {'type': kind if kind == 'dm' else 'chat', **place, 'from': event['from'][:16],
             'short': str(event.get('short') or '')[:8], 'long': str(event.get('long') or '')[:40],
             'text': event['text'][:MAX_TEXT], 'rx_time': rx_time, 'mine': bool(event.get('mine'))}
    if clean['mine']:
        packet_id = event.get('id')
        clean['id'] = packet_id if isinstance(packet_id, int) and not isinstance(packet_id, bool) else 0
        clean['status'] = event.get('status') if isinstance(event.get('status'), str) and event.get('status') in STATUSES else 'unconfirmed'
        clean['reason'] = str(event.get('reason') or '')[:40]
    return clean


class ChatLog:
    """Bounded history per radio channel, optionally kept in a local JSON file across restarts."""

    def __init__(self, limit=200, path=None):
        self.limit = limit
        self.path = Path(path) if path else None
        self._messages = {}
        if self.path is not None:
            self._load()

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            stored = data['messages']
            if data.get('version') != 1 or not isinstance(stored, dict):
                raise ValueError('unsupported chat history format')
        except FileNotFoundError:
            return
        except (OSError, ValueError, KeyError, TypeError):
            logging.warning('Chat history unreadable; kept as %s.bad and starting fresh.', self.path.name)
            try:
                # Never overwrite an earlier damaged-history backup.
                backup = self.path.with_name(self.path.name + '.bad')
                suffix = 1
                while backup.exists():
                    backup = self.path.with_name(f'{self.path.name}.bad.{suffix}')
                    suffix += 1
                os.replace(self.path, backup)
            except OSError:
                pass
            return
        # Iterate over a bounded tail of each channel, even if an external file
        # contains far more rows than our configured retention limit.
        for rows in stored.values():
            if not isinstance(rows, list):
                continue
            for row in rows[-self.limit:]:
                event = clean_event(row) if isinstance(row, dict) else None
                if event is not None and event.get('status') == 'sending':
                    event['status'] = 'unconfirmed'     # its reply was lost with the old session
                if event is not None:
                    self._messages.setdefault(event_key(event), deque(maxlen=self.limit)).append(event)

    def _save(self):
        if self.path is None:
            return
        payload = {'version': 1, 'messages': {str(ch): list(rows) for ch, rows in self._messages.items()}}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_name(self.path.name + '.tmp')
            temp.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
            os.replace(temp, self.path)
        except OSError:
            logging.warning('Could not save chat history; messages stay in memory for this session.')

    def add(self, event):
        event = clean_event(event)
        if event is None:
            return False
        self._messages.setdefault(event_key(event), deque(maxlen=self.limit)).append(event)
        self._save()
        return True

    def set_status(self, packet_id, status, reason=''):
        """Record the radio's reply for one of our sent messages; the updated message, or None."""
        if not isinstance(packet_id, int) or not packet_id or status not in STATUSES:
            return None
        for rows in self._messages.values():
            for event in reversed(rows):
                if event.get('mine') and event.get('id') == packet_id:
                    # A late or duplicate callback must not erase a final radio result.
                    if event.get('status') in FINAL:
                        return None
                    if status == 'sending':
                        return None
                    event['status'], event['reason'] = status, str(reason or '')[:40]
                    self._save()
                    return event
        return None

    def clear(self):
        self._messages = {}
        self._save()

    def channels(self):
        return sorted(key for key in self._messages if isinstance(key, int))

    def peers(self):
        """Direct-message peers, most recent conversation first."""
        dms = [(rows[-1]['rx_time'], dm_peer(key)) for key, rows in self._messages.items() if dm_peer(key) and rows]
        return [peer for _when, peer in sorted(dms, reverse=True)]

    def messages(self, channel):
        return list(self._messages.get(channel, ()))

    def render(self, channel, name):
        rows = self.messages(channel)
        kept = 'kept between restarts' if self.path is not None else 'this session'
        if dm_peer(channel):
            header = (f'{name} — direct messages, {kept} (last {len(rows)}).\n'
                      'After you send, their replies come here for 10 minutes instead of the BBS menu. '
                      "Replies starting with / still go to the BBS.\n\n")
            if not rows:
                return header + 'No messages yet. Type below to start a conversation.'
            return header + '\n'.join(format_message(row) for row in rows)
        header = (f'{name} — channel messages, {kept} (last {len(rows)}).\n'
                  'Direct messages to the BBS are handled by the BBS.\n\n')
        if not rows:
            return header + 'No messages yet. Start the BBS server to listen on this channel.'
        return header + '\n'.join(format_message(row) for row in rows)
