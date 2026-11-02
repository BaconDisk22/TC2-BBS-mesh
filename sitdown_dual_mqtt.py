"""Opt-in, independent, receive-only MQTT monitor sessions.

Incoming payloads are parsed for metadata only and queued for the desktop.
Nothing here publishes, decrypts, bridges, or transmits Meshtastic packets.
The existing legacy mqtt_monitor remains unchanged.
"""
import configparser
from collections import Counter
from threading import Lock
from sitdown_mqtt_event_queue import MQTTEventQueue
from sitdown_mqtt_pipeline import MQTTMessagePipeline
from sitdown_online import ActivityCounter, topic_list

NETWORKS = {
    'centralnj': ('mqtt_centralnj', 'mqtt.meshtastic.org'),
    'sjmesh': ('mqtt_sjmesh', 'mqtt.sjmesh.net'),
}


def topic_allowed(topic):
    """Accept one exact topic, or every gateway on one named encrypted channel.

    The only wildcard allowed is a single trailing '+' in the gateway position
    of a Meshtastic channel topic, e.g. msh/US/2/e/CentralNJ/+. '#', any other
    '+', and broad region topics stay refused.
    """
    if not topic or any(ch.isspace() for ch in topic) or '#' in topic:
        return False
    if '+' not in topic:
        return True
    parts = topic.split('/')
    return (parts[-1] == '+' and '+' not in ''.join(parts[:-1])
            and len(parts) >= 6 and parts[0] == 'msh'
            and parts[-4:-2] == ['2', 'e'] and all(parts[:-1]))


MAX_TOPICS = 4


def topics_allowed(value):
    """1–4 distinct comma-separated topics, each passing topic_allowed."""
    topics = topic_list(value)
    return (0 < len(topics) <= MAX_TOPICS and len(set(topics)) == len(topics)
            and all(topic_allowed(topic) for topic in topics))


def network_config(source, network):
    """Return an isolated ActivityCounter config; never fill in credentials."""
    section, default_host = NETWORKS[network]
    config = configparser.ConfigParser()
    enabled = source.getboolean(section, 'enabled', fallback=False)
    topic = source.get(section, 'topic', fallback='').strip()
    host = source.get(section, 'host', fallback=default_host).strip()
    config['mqtt_monitor'] = {
        'enabled': str(enabled).lower(),
        'host': host,
        'topic': topic,
        'tls': source.get(section, 'tls', fallback='true'),
        'port': source.get(section, 'port', fallback='8883'),
        'username': source.get(section, 'username', fallback=''),
        'password': source.get(section, 'password', fallback=''),
    }
    return config


class DualMQTTMonitors:
    """Own separate Paho clients and counters for each opt-in broker."""

    def __init__(self, config, pipeline=None, events=None):
        self.config = config
        self.pipeline = pipeline or MQTTMessagePipeline()
        self.events = events or MQTTEventQueue()
        # Each broker runs its own Paho thread; the shared deduplicator is not thread-safe.
        self._pipeline_lock = Lock()
        self._outcomes = {name: Counter() for name in NETWORKS}
        self.monitors = {
            name: ActivityCounter(network_config(config, name),
                                  sink=lambda payload, n=name: self.receive(n, payload))
            for name in NETWORKS
        }

    def receive(self, network, payload):
        """Process one broker payload on its Paho thread and queue the result for the UI."""
        with self._pipeline_lock:
            result = self.pipeline.process(network, payload)
            if network in self._outcomes:
                self._outcomes[network]['accepted' if result.accepted else result.reason] += 1
        self.events.push(network, result)
        return result

    def ingest_stats(self):
        """Per-network processing outcomes since this session started."""
        with self._pipeline_lock:
            outcomes = {name: Counter(counts) for name, counts in self._outcomes.items()}
        stats = {}
        for name, counts in outcomes.items():
            accepted = counts.pop('accepted', 0)
            duplicates = counts.pop('Duplicate packet', 0)
            stats[name] = {'accepted': accepted, 'duplicates': duplicates,
                           'rejected': dict(counts), 'queue_dropped': self.events.dropped_for(name)}
        return stats

    def drain_events(self, limit=50):
        """Called from the Tk thread; never blocks on broker I/O."""
        return self.events.drain(limit)

    def start(self, factories=None):
        """Start only explicitly enabled networks with configured narrow topics."""
        factories = factories or {}
        for name, monitor in self.monitors.items():
            if not monitor.config.getboolean('mqtt_monitor', 'enabled'):
                continue
            self.connect(name, factory=factories.get(name))

    def connect(self, network, factory=None):
        """Connect one explicitly enabled profile; never connect the other."""
        monitor = self.monitors[network]
        if not monitor.config.getboolean('mqtt_monitor', 'enabled', fallback=False):
            monitor.status = 'Disabled — enable profile first'
            return False
        topic = monitor.config.get('mqtt_monitor', 'topic', fallback='').strip()
        if not topics_allowed(topic):
            monitor.status = 'Disabled — explicit narrow topic required'
            return False
        host = monitor.config.get('mqtt_monitor', 'host', fallback='').strip()
        if not host or any(ch.isspace() for ch in host):
            monitor.status = 'Disabled — valid broker hostname required'
            return False
        try:
            port = monitor.config.getint('mqtt_monitor', 'port', fallback=8883)
            if not 1 <= port <= 65535:
                raise ValueError('port out of range')
        except ValueError:
            monitor.status = 'Disabled — invalid broker port'
            return False
        monitor.start(factory=factory)
        return monitor.client is not None

    def stop(self, network=None):
        if network is not None:
            self.monitors[network].stop()
        else:
            for monitor in self.monitors.values():
                monitor.stop()

    def snapshot(self):
        return {name: monitor.snapshot() for name, monitor in self.monitors.items()}

    def diagnostics(self):
        return {name: monitor.diagnostics() for name, monitor in self.monitors.items()}
