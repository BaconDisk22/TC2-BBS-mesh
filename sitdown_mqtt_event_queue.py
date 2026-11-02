"""Thread-safe, bounded receive-only MQTT UI event handoff."""
from collections import Counter, deque
from threading import Lock


class MQTTEventQueue:
    def __init__(self, capacity=256):
        if capacity < 1:
            raise ValueError('capacity must be positive')
        self.capacity = capacity
        self._events = deque()
        self._lock = Lock()
        self.dropped = 0
        self.dropped_by_network = Counter()

    def push(self, network, result):
        if network not in ('centralnj', 'sjmesh'):
            return False
        with self._lock:
            if len(self._events) >= self.capacity:
                lost, _ = self._events.popleft()
                self.dropped += 1
                self.dropped_by_network[lost] += 1
            self._events.append((network, result))
        return True

    def drain(self, limit=50):
        if limit < 1:
            return []
        with self._lock:
            count = min(limit, len(self._events))
            return [self._events.popleft() for _ in range(count)]

    def stats(self):
        with self._lock:
            return {'queued': len(self._events), 'dropped': self.dropped}

    def dropped_for(self, network):
        with self._lock:
            return self.dropped_by_network[network]
