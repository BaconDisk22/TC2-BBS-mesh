"""Per-network, bounded receive-only MQTT activity for the desktop.

Shows packet metadata only. Message content is never decrypted, inferred,
or displayed, and nothing here can publish or forward to a radio.
"""
import time
from collections import deque
from dataclasses import dataclass

NETWORK_LABELS = {'centralnj': 'CentralNJ', 'sjmesh': 'SJMesh'}
BROADCAST = 0xFFFFFFFF


@dataclass(frozen=True)
class FeedEntry:
    received_at: float
    result: object


def node_label(number):
    return 'broadcast' if number == BROADCAST else f'!{number:08x}'


def port_label(portnum):
    try:
        from meshtastic.protobuf import portnums_pb2
        return portnums_pb2.PortNum.Name(portnum)
    except (ImportError, ValueError):
        return f'port {portnum}'


def format_stats(stats):
    """One line of live ingest health for a network; never includes payloads."""
    rejected = stats['rejected']
    detail = ', '.join(f'{reason.lower()} {n}' for reason, n in sorted(rejected.items()))
    return (f"Accepted {stats['accepted']} · duplicates {stats['duplicates']} · "
            f"rejected {sum(rejected.values())}" + (f' ({detail})' if detail else '')
            + f" · queue overflow {stats['queue_dropped']}")


def format_entry(entry):
    stamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(entry.received_at))
    result = entry.result
    if not result.accepted:
        return f'{stamp}  REJECTED — {result.reason}'
    s = result.summary
    state = ('ENCRYPTED — not decrypted' if s.encrypted
             else f'{port_label(s.portnum)} — content not displayed')
    return (f'{stamp}  from {node_label(s.from_id)} → {node_label(s.to_id)}'
            f'  ch {s.channel_id}  via {s.gateway_id or "unknown gateway"}\n    {state}')


class MeshFeed:
    """Recent activity kept separately per network; duplicates are not listed."""

    def __init__(self, limit=100, clock=time.time):
        self.clock = clock
        self._entries = {name: deque(maxlen=limit) for name in NETWORK_LABELS}

    def add(self, network, result):
        if network not in self._entries or result.reason == 'Duplicate packet':
            return False
        self._entries[network].append(FeedEntry(self.clock(), result))
        return True

    def entries(self, network):
        return list(reversed(self._entries[network]))

    def render(self, network, status=None, stats=None):
        rows = self.entries(network)
        header = (f'{NETWORK_LABELS[network]} — receive-only MQTT activity, newest first '
                  f'(last {len(rows)}).\nMetadata only: sender, channel, gateway, time received, '
                  'encryption state.\nMQTT activity is not radio RX and is never forwarded.\n')
        if status is not None:
            header += f'Connection: {status}\n'
        if stats is not None:
            header += format_stats(stats) + '\n'
        header += '\n'
        if not rows:
            return header + 'No packets received this session. Connect this network in Settings.'
        return header + '\n'.join(format_entry(row) for row in rows)
