"""Optional internet services. Constructing them never opens a connection."""
from collections import Counter
import json
import logging
import math
import threading
import time
from sitdown_mqtt_recovery_state import RecoveryState
from urllib.parse import urlencode
from urllib.request import urlopen
from sitdown_tls import ca_file, ssl_context


def https_open(url, timeout):
    """urlopen with a CA list that exists inside the packaged app."""
    return urlopen(url, timeout=timeout, context=ssl_context())


class WeatherDesk:
    def __init__(self, config, opener=https_open, clock=time.monotonic):
        self.config, self.opener, self.clock = config, opener, clock
        self.cached = None
        self.checked = None
        self.lock = threading.Lock()

    def report(self):
        c = self.config
        local = c.get('weather', 'local_report', fallback='').strip()
        try:
            enabled = c.getboolean('weather', 'internet_enabled', fallback=False)
        except ValueError:
            return 'Jersey Weather Desk: invalid internet setting; no request sent.'
        if not enabled:
            return 'Jersey Weather Desk — local report: ' + local if local else 'Jersey Weather Desk: internet weather is disabled; no local report configured.'
        with self.lock:
            now = self.clock()
            if self.checked is not None and now - self.checked < 600:
                return self.cached or 'Weather unavailable; retry after the ten-minute cooldown.'
            self.checked = now
            try:
                lat = c.getfloat('weather', 'latitude')
                lon = c.getfloat('weather', 'longitude')
                if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
                    raise ValueError('Invalid coordinates')
                query = urlencode({'latitude': lat, 'longitude': lon,
                    'current': 'temperature_2m,weather_code,wind_speed_10m',
                    'temperature_unit': 'fahrenheit', 'wind_speed_unit': 'mph', 'timezone': 'UTC'})
                with self.opener('https://api.open-meteo.com/v1/forecast?' + query, timeout=5) as response:
                    payload = response.read(65537)
                if len(payload) > 65536:
                    raise ValueError('Response too large')
                current = json.loads(payload)['current']
                values = [current[k] for k in ('temperature_2m', 'wind_speed_10m', 'weather_code')]
                if not all(isinstance(v, (float, int)) and math.isfinite(v) for v in values):
                    raise ValueError('Missing weather values')
                self.cached = (f"Jersey Weather Desk ({lat}, {lon}) — Open-Meteo\n"
                    f"{current['time']} UTC: {values[0]} F, wind {values[1]} mph, WMO code {values[2]}.")
                return self.cached
            except Exception:
                logging.warning('Internet weather unavailable; retaining local/cached weather.')
                if self.cached:
                    if not self.cached.startswith('STALE'):
                        self.cached = 'STALE — last successful report:\n' + self.cached
                    return self.cached
                return 'Internet weather unavailable.' + (' Local report: ' + local if local else '')


def topic_list(value):
    """Split a comma-separated topic setting into its non-empty entries."""
    return [topic.strip() for topic in (value or '').split(',') if topic.strip()]


class ActivityCounter:
    """Subscribe-only counter; never forwards MQTT payloads to a radio."""
    def __init__(self, config, sink=None):
        self.config = config
        self.sink = sink  # optional receive-only payload handler
        self.client = None
        self.status = 'Disabled'
        self.counts = Counter()
        self.total = 0
        self.overflow = 0
        self.lock = threading.Lock()
        self.topic = ''
        self.recovery = RecoveryState()

    def start(self, factory=None):
        c = self.config
        if self.client is not None:
            return
        try:
            if not c.getboolean('mqtt_monitor', 'enabled', fallback=False):
                return
            host = c.get('mqtt_monitor', 'host', fallback='').strip()
            self.topic = c.get('mqtt_monitor', 'topic', fallback='').strip()
            if not host or not topic_list(self.topic):
                raise ValueError('Configure host and topic explicitly')
            if factory is None:
                import paho.mqtt.client as mqtt
                factory = lambda: mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            client = factory()
            self.client = client
            self.recovery = RecoveryState(enabled=True)
            client.on_connect = self._connect
            client.on_disconnect = self._disconnect
            client.on_connect_fail = self._connect_fail
            client.on_message = self._message
            username = c.get('mqtt_monitor', 'username', fallback='')
            if username:
                client.username_pw_set(username, c.get('mqtt_monitor', 'password', fallback=''))
            tls = c.getboolean('mqtt_monitor', 'tls', fallback=True)
            if tls:
                client.tls_set(ca_certs=ca_file())
            port = c.getint('mqtt_monitor', 'port', fallback=8883 if tls else 1883)
            client.reconnect_delay_set(min_delay=5, max_delay=120)
            self.status = 'Connecting'
            client.connect_async(host, port, 60)
            client.loop_start()
        except Exception:
            self.status = 'Unavailable — check MQTT configuration/dependency'
            logging.warning('MQTT unavailable; radio BBS continues independently.')
            self.stop(preserve_status=True)

    def _connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            self.recovery.connected()
            results = [client.subscribe(topic)[0] for topic in topic_list(self.topic)]
            self.status = ('Subscription requested (not yet confirmed)' if results and all(r == 0 for r in results)
                           else 'Subscription failed')
        else:
            self.status = 'Connection refused'

    def _disconnect(self, client, userdata, flags, reason_code, properties):
        self.status = 'Disconnected'

    def _connect_fail(self, client, userdata):
        self.recovery.next_delay()  # telemetry only; Paho owns reconnect timing
        self.status = 'Unavailable — reconnecting'

    def _message(self, client, userdata, message):
        # Retained history is not new activity. Keep bounded topic storage.
        if message.retain:
            return
        with self.lock:
            self.total += 1
            if message.topic in self.counts or len(self.counts) < 1000:
                self.counts[message.topic] += 1
            else:
                self.overflow += 1
        if self.sink is not None:
            try:
                self.sink(getattr(message, 'payload', b''))
            except Exception:
                logging.warning('MQTT payload processing failed; counter unaffected.')

    def diagnostics(self):
        """Credential-free summary of the optional MQTT counter configuration."""
        c = self.config
        try:
            enabled = c.getboolean('mqtt_monitor', 'enabled', fallback=False)
        except ValueError:
            return 'The Wire: invalid enable setting; MQTT monitor not ready.'
        if not enabled:
            return 'The Wire: MQTT monitor disabled (radio messaging unaffected).'
        host = c.get('mqtt_monitor', 'host', fallback='').strip()
        topic = c.get('mqtt_monitor', 'topic', fallback='').strip()
        if not host or not topic:
            return 'The Wire: configure both broker hostname and a scoped subscription topic.'
        if topic in ('#', 'msh/#', 'msh/US/#'):
            return 'The Wire: broad MQTT subscription; prefer a narrowly scoped authorized topic.'
        try:
            port = c.getint('mqtt_monitor', 'port', fallback=8883)
            if not 1 <= port <= 65535:
                raise ValueError()
        except ValueError:
            return 'The Wire: invalid MQTT port (must be 1–65535).'
        return 'The Wire: monitor configured; status: ' + self.status + '. This counter cannot send radio messages.'

    def snapshot(self):
        with self.lock:
            return {'status': self.status, 'recovery_attempts': self.recovery.attempts,
                    'recovery_stopped': self.recovery.stopped,
                    'total': self.total, 'overflow': self.overflow,
                    'topics': self.counts.most_common(10)}

    def report(self):
        data = self.snapshot()
        return (f"The Wire: {data['status']}; {data['total']} messages this session "
                '(MQTT only, not radio RX).\n' + '\n'.join(f'{n}: {topic}' for topic, n in data['topics']))

    def stop(self, preserve_status=False):
        self.recovery.stop()
        if self.client is not None:
            try:
                self.client.disconnect()
            except Exception:
                logging.warning('MQTT disconnect failed.')
            finally:
                try:
                    self.client.loop_stop()
                except Exception:
                    logging.warning('MQTT loop shutdown failed.')
                self.client = None
        if not preserve_status:
            self.status = 'Stopped'
