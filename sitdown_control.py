"""Private parent/child pipe protocol; no listening socket or internet endpoint."""
import json
import os
import queue
import sys
import threading
from sitdown_utilities import lookup_nodes

PREFIX = 'SITDOWN_EVENT '
_emit_lock = threading.Lock()


def emit(payload):
    """Write one complete event line; safe from the radio receive thread too."""
    token = os.environ.get('SITDOWN_CONTROL_TOKEN', '')
    line = PREFIX + token + ' ' + json.dumps(payload, default=_json_safe) + '\n'
    with _emit_lock:
        sys.stdout.write(line)
        sys.stdout.flush()


def _json_safe(value):
    """Convert protobuf messages and enum-like values for desktop telemetry."""
    from google.protobuf.json_format import MessageToDict
    from google.protobuf.message import Message
    if isinstance(value, Message):
        return MessageToDict(value, preserving_proto_field_name=True)
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    raise TypeError(f'Unsupported desktop telemetry type: {type(value).__name__}')


def start_reader(stream=None):
    requests = queue.Queue(maxsize=20)
    def read():
        for line in stream or sys.stdin:
            try:
                request = json.loads(line)
                if isinstance(request, dict):
                    requests.put_nowait(request)
            except (ValueError, queue.Full):
                emit({'type': 'result', 'error': 'Invalid request or server busy.'})
    threading.Thread(target=read, daemon=True).start()
    return requests


def snapshot(service):
    return {'type': 'snapshot', 'nodes': lookup_nodes(service.interface.nodes),
            'channels': service.channels(), 'mqtt': service.mqtt.snapshot()}


def handle(service, request):
    """Only the local process owner can write this pipe."""
    action = request.get('action')
    try:
        if action == 'announce':
            result = service.announce(request.get('channel'), request.get('text', ''), local=True)
        elif action == 'chat':
            event = service.chat_send(request.get('channel'), request.get('text', ''), on_status=emit)
            return {'type': 'result', 'action': action,
                    'text': 'Chat message submitted to radio; waiting for a relay.', 'chat': event}
        elif action == 'weather':
            result = service.weather.report()
        elif action == 'snapshot':
            return snapshot(service)
        else:
            raise ValueError('Unknown desktop operation.')
        return {'type': 'result', 'action': action, 'text': result}
    except Exception as exc:
        return {'type': 'result', 'action': action, 'error': str(exc)}
