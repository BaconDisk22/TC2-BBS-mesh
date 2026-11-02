"""Radio channel chat for the desktop.

Channel (broadcast) text messages heard by the BBS radio are passed to the
desktop for display. Direct messages stay with the BBS command handler.
Sending is operator-initiated only, gated on the desktop and rate limited here.
"""
import json
import logging
import os
import time
from collections import deque
from pathlib import Path

BROADCAST = 0xFFFFFFFF
MAX_TEXT = 500
MAX_CHANNELS = 8
STATUSES = ('sending', 'relayed', 'failed', 'unconfirmed')
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
    user = (nodes.get(sender) or {}).get('user') or {}
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
    return {'sending': '… sending', 'relayed': '✓ relayed', 'unconfirmed': '? no confirmation',
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


def clean_event(event):
    """A chat event reduced to known fields with checked types, or None."""
    try:
        channel = int(event['channel'])
        valid = (event.get('type') == 'chat' and 0 <= channel < MAX_CHANNELS
                 and isinstance(event.get('text'), str) and isinstance(event.get('from'), str))
        rx_time = int(event.get('rx_time') or 0)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    if not valid:
        return None
    clean = {'type': 'chat', 'channel': channel, 'from': event['from'][:16],
             'short': str(event.get('short') or '')[:8], 'long': str(event.get('long') or '')[:40],
             'text': event['text'][:MAX_TEXT], 'rx_time': rx_time, 'mine': bool(event.get('mine'))}
    if clean['mine']:
        packet_id = event.get('id')
        clean['id'] = packet_id if isinstance(packet_id, int) and not isinstance(packet_id, bool) else 0
        clean['status'] = event.get('status') if event.get('status') in STATUSES else 'unconfirmed'
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
                os.replace(self.path, self.path.with_name(self.path.name + '.bad'))
            except OSError:
                pass
            return
        for rows in stored.values():
            for row in rows if isinstance(rows, list) else ():
                event = clean_event(row) if isinstance(row, dict) else None
                if event is not None and event.get('status') == 'sending':
                    event['status'] = 'unconfirmed'     # its reply was lost with the old session
                if event is not None:
                    self._messages.setdefault(event['channel'], deque(maxlen=self.limit)).append(event)

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
        self._messages.setdefault(event['channel'], deque(maxlen=self.limit)).append(event)
        self._save()
        return True

    def set_status(self, packet_id, status, reason=''):
        """Record the radio's reply for one of our sent messages; the updated message, or None."""
        if not isinstance(packet_id, int) or not packet_id or status not in STATUSES:
            return None
        for rows in self._messages.values():
            for event in reversed(rows):
                if event.get('mine') and event.get('id') == packet_id:
                    event['status'], event['reason'] = status, str(reason or '')[:40]
                    self._save()
                    return event
        return None

    def clear(self):
        self._messages = {}
        self._save()

    def channels(self):
        return sorted(self._messages)

    def messages(self, channel):
        return list(self._messages.get(channel, ()))

    def render(self, channel, name):
        rows = self.messages(channel)
        kept = 'kept between restarts' if self.path is not None else 'this session'
        header = (f'{name} — channel messages, {kept} (last {len(rows)}).\n'
                  'Direct messages to the BBS are handled by the BBS.\n\n')
        if not rows:
            return header + 'No messages yet. Start the BBS server to listen on this channel.'
        return header + '\n'.join(format_message(row) for row in rows)
