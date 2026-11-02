"""Bounded in-memory packet duplicate tracking; no network side effects."""
import time
from collections import OrderedDict


class PacketDeduplicator:
    def __init__(self, max_entries=2048, ttl_seconds=600, clock=None):
        if max_entries < 1 or ttl_seconds <= 0:
            raise ValueError("Invalid duplicate cache limits")
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self.clock = clock or time.monotonic
        self._seen = OrderedDict()

    def duplicate(self, network, channel, source, packet_id):
        """Return True only if the exact network/channel/source/id was seen recently."""
        if not network or not channel or not source or not packet_id:
            return False
        now = self.clock()
        key = (network, channel, source, packet_id)
        for old_key, expires in list(self._seen.items()):
            if expires <= now:
                del self._seen[old_key]
        if key in self._seen:
            return True
        self._seen[key] = now + self.ttl_seconds
        while len(self._seen) > self.max_entries:
            self._seen.popitem(last=False)
        return False
