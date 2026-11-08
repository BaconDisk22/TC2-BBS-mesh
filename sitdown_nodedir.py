"""The desktop's own node directory, beyond the radio's fixed-size node list.

A Meshtastic radio remembers a limited number of nodes (100 on an ESP32 without
PSRAM) and forgets the least recently heard. The desktop keeps every node it
learns about, from the radio's node list and from MQTT packets, in a local
SQLite file, with when and where (radio / MQTT network) each was last heard.

Only metadata is stored: id, names, hardware, role, signal, hops, battery and
position from the radio's own node list, and ids/names seen on MQTT. All writes
happen on the Tk thread.
"""
import math
import sqlite3
import time
from pathlib import Path

from sitdown_dm import peer_id

MAX_NODES = 20000
SOURCES = ('radio', 'centralnj', 'sjmesh')
SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    short TEXT NOT NULL DEFAULT '',
    long TEXT NOT NULL DEFAULT '',
    hardware TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT '',
    first_heard INTEGER NOT NULL,
    last_heard INTEGER NOT NULL,
    radio_heard INTEGER,
    centralnj_heard INTEGER,
    sjmesh_heard INTEGER,
    snr REAL,
    hops INTEGER,
    battery INTEGER,
    latitude REAL,
    longitude REAL
);
CREATE INDEX IF NOT EXISTS nodes_last_heard ON nodes(last_heard);
"""
COLUMNS = ('id', 'short', 'long', 'hardware', 'role', 'first_heard', 'last_heard', 'radio_heard',
           'centralnj_heard', 'sjmesh_heard', 'snr', 'hops', 'battery', 'latitude', 'longitude')


def _number(value, kind=float):
    try:
        if isinstance(value, bool) or value is None:
            return None
        result = kind(value)
    except (TypeError, ValueError):
        return None
    return result if not (isinstance(result, float) and math.isnan(result)) else None


def _text(value, limit):
    return str(value or '')[:limit]


def radio_row(row, now):
    """Directory fields from one node in the radio snapshot, or None when it has no usable id."""
    if not isinstance(row, dict):
        return None
    identity = peer_id(row.get('id'))
    if identity is None:
        return None
    heard = _number(row.get('lastHeard'), int)
    heard = heard if heard and 0 < heard <= now + 300 else None
    metrics = row.get('deviceMetrics') if isinstance(row.get('deviceMetrics'), dict) else {}
    position = row.get('position') if isinstance(row.get('position'), dict) else {}
    latitude = _number(position.get('latitude'))
    longitude = _number(position.get('longitude'))
    if latitude is None and position.get('latitudeI') is not None:
        latitude = _number(position.get('latitudeI'))
        latitude = latitude / 1e7 if latitude is not None else None
    if longitude is None and position.get('longitudeI') is not None:
        longitude = _number(position.get('longitudeI'))
        longitude = longitude / 1e7 if longitude is not None else None
    if latitude is not None and not -90 <= latitude <= 90:
        latitude = None
    if longitude is not None and not -180 <= longitude <= 180:
        longitude = None
    battery = _number(metrics.get('batteryLevel'), int)
    return {'id': identity, 'short': _text(row.get('shortName'), 8), 'long': _text(row.get('longName'), 40),
            'hardware': _text(row.get('hardware'), 32), 'role': _text(row.get('role'), 24),
            'heard': heard, 'snr': _number(row.get('snr')), 'hops': _number(row.get('hopsAway'), int),
            'battery': battery if battery is not None and 0 <= battery <= 101 else None,
            'latitude': latitude, 'longitude': longitude}


class NodeDirectory:
    """Every node heard by the radio or on MQTT, kept between restarts."""

    def __init__(self, path=None, clock=time.time, limit=MAX_NODES, readonly=False):
        self.clock, self.limit = clock, limit
        if readonly:
            # The BBS server only reads the desktop's list; it never creates or changes it.
            self.db = sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True)
            return
        self.db = sqlite3.connect(str(path) if path else ':memory:')
        if path:
            try:
                Path(path).chmod(0o600)
            except OSError:
                pass
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    def record_radio(self, nodes):
        """Merge the radio's node list (the snapshot sent every few seconds). Returns rows changed."""
        now = int(self.clock())
        changed = 0
        with self.db:
            for raw in nodes or []:
                row = radio_row(raw, now)
                if row is None:
                    continue
                heard = row['heard'] or None
                existing = self.db.execute('SELECT last_heard, radio_heard FROM nodes WHERE id = ?',
                                           (row['id'],)).fetchone()
                if existing is None:
                    stamp = heard or now
                    self.db.execute(
                        'INSERT INTO nodes (id, short, long, hardware, role, first_heard, last_heard, radio_heard, '
                        'snr, hops, battery, latitude, longitude) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (row['id'], row['short'], row['long'], row['hardware'], row['role'], stamp, stamp, heard,
                         row['snr'], row['hops'], row['battery'], row['latitude'], row['longitude']))
                    changed += 1
                    continue
                last_heard, radio_heard = existing
                newer = heard is not None and heard > (radio_heard or 0)
                # Names and hardware always refresh; signal/position only when the radio heard it more recently.
                self.db.execute(
                    "UPDATE nodes SET short = CASE WHEN ? != '' THEN ? ELSE short END, "
                    "long = CASE WHEN ? != '' THEN ? ELSE long END, "
                    "hardware = CASE WHEN ? != '' THEN ? ELSE hardware END, "
                    "role = CASE WHEN ? != '' THEN ? ELSE role END WHERE id = ?",
                    (row['short'], row['short'], row['long'], row['long'], row['hardware'], row['hardware'],
                     row['role'], row['role'], row['id']))
                if newer:
                    self.db.execute(
                        'UPDATE nodes SET radio_heard = ?, last_heard = MAX(last_heard, ?), snr = ?, hops = ?, '
                        'battery = COALESCE(?, battery), latitude = COALESCE(?, latitude), '
                        'longitude = COALESCE(?, longitude) WHERE id = ?',
                        (heard, heard, row['snr'], row['hops'], row['battery'], row['latitude'], row['longitude'],
                         row['id']))
                changed += 1
            self._prune()
        return changed

    def record_mqtt(self, network, number, short='', long=''):
        """Note that a node sent a packet seen on an MQTT network (optionally with its names)."""
        return self.record_mqtt_many([(network, number, short, long)]) == 1

    def record_mqtt_many(self, sightings):
        """Record (network, node number, short, long[, details]) sightings in one transaction.

        details may carry latitude/longitude/battery opened from POSITION or TELEMETRY packets.
        Returns how many sightings were valid.
        """
        now = int(self.clock())
        stored = 0
        with self.db:
            for sighting in sightings:
                network, number, short, long = sighting[:4]
                details = sighting[4] if len(sighting) > 4 and isinstance(sighting[4], dict) else {}
                identity = peer_id(number)
                if network not in SOURCES[1:] or identity is None:
                    continue
                column = f'{network}_heard'
                self.db.execute(
                    f'INSERT INTO nodes (id, short, long, first_heard, last_heard, {column}) VALUES (?,?,?,?,?,?) '
                    f'ON CONFLICT(id) DO UPDATE SET last_heard = MAX(last_heard, excluded.last_heard), '
                    f'{column} = excluded.{column}, '
                    "short = CASE WHEN excluded.short != '' THEN excluded.short ELSE short END, "
                    "long = CASE WHEN excluded.long != '' THEN excluded.long ELSE long END",
                    (identity, _text(short, 8), _text(long, 40), now, now, now))
                latitude, longitude = _number(details.get('latitude')), _number(details.get('longitude'))
                if latitude is not None and longitude is not None and -90 <= latitude <= 90 and -180 <= longitude <= 180:
                    self.db.execute('UPDATE nodes SET latitude = ?, longitude = ? WHERE id = ?',
                                    (latitude, longitude, identity))
                battery = _number(details.get('battery'), int)
                if battery is not None and 0 <= battery <= 101:
                    self.db.execute('UPDATE nodes SET battery = ? WHERE id = ?', (battery, identity))
                stored += 1
            if stored:
                self._prune()
        return stored

    def _prune(self):
        count = self.db.execute('SELECT COUNT(*) FROM nodes').fetchone()[0]
        if count > self.limit:
            self.db.execute('DELETE FROM nodes WHERE id IN (SELECT id FROM nodes ORDER BY last_heard ASC LIMIT ?)',
                            (count - self.limit,))

    def count(self):
        return self.db.execute('SELECT COUNT(*) FROM nodes').fetchone()[0]

    def search(self, query='', since=None, source='all', limit=500):
        """Nodes matching a name/id search, heard since a time, from a source; newest first."""
        clauses, params = [], []
        text = (query or '').strip()
        if text:
            identity = peer_id(text)
            like = f"%{text.casefold().replace('%', '').replace('_', '')}%"
            if identity:
                clauses.append('(id = ? OR lower(short) LIKE ? OR lower(long) LIKE ?)')
                params += [identity, like, like]
            else:
                clauses.append('(lower(short) LIKE ? OR lower(long) LIKE ? OR id LIKE ?)')
                params += [like, like, like]
        if since is not None:
            clauses.append('last_heard >= ?')
            params.append(int(since))
        if source == 'radio':
            clauses.append('radio_heard IS NOT NULL')
        elif source == 'mqtt':
            clauses.append('(centralnj_heard IS NOT NULL OR sjmesh_heard IS NOT NULL)')
        where = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
        total = self.db.execute('SELECT COUNT(*) FROM nodes' + where, params).fetchone()[0]
        rows = self.db.execute(f"SELECT {', '.join(COLUMNS)} FROM nodes{where} ORDER BY last_heard DESC LIMIT ?",
                               params + [limit]).fetchall()
        return total, [dict(zip(COLUMNS, row)) for row in rows]


def directory_path(config_file):
    """Where the desktop keeps its node list: beside config.ini (packaged app) or in ~/.sitdown-bbs."""
    beside = Path(config_file).resolve().with_name('node-directory.db') if config_file else None
    if beside is not None and beside.exists():
        return beside
    return Path.home() / '.sitdown-bbs' / 'node-directory.db'


def radio_reply_line(row, now):
    """One short line for a /node reply over the radio."""
    name = ' '.join(part for part in (row['short'], f"({row['long']})" if row['long'] else '') if part) or 'Unnamed'
    where = ', '.join(label for label, column in (('radio', 'radio_heard'), ('CentralNJ', 'centralnj_heard'),
                                                  ('SJMesh', 'sjmesh_heard')) if row[column])
    return f"{name} {row['id']} - last heard {ago(now - row['last_heard'])}" + (f' via {where}' if where else '')


def ago(seconds):
    seconds = max(0, int(seconds))
    if seconds < 90:
        return 'just now'
    for size, unit in ((86400, 'day'), (3600, 'hour'), (60, 'minute')):
        if seconds >= size:
            amount = seconds // size
            return f"{amount} {unit}{'s' if amount != 1 else ''} ago"
    return 'just now'


def format_entry(row, now, radio_ids=()):
    """Readable lines for one directory node; the first line starts with a timestamp-free name line."""
    name = ' '.join(part for part in (row['short'], f"({row['long']})" if row['long'] else '') if part) or 'Unnamed'
    sources = []
    if row['id'] in radio_ids:
        sources.append('in radio list')
    elif row['radio_heard']:
        sources.append('heard by radio')
    if row['centralnj_heard']:
        sources.append('CentralNJ')
    if row['sjmesh_heard']:
        sources.append('SJMesh')
    details = [f"last heard {ago(now - row['last_heard'])}", ' · '.join(sources) or 'unknown source']
    extras = [value for value in (
        row['hardware'] or None,
        f"{row['battery']}% battery" if row['battery'] is not None else None,
        f"SNR {row['snr']:.1f}" if row['snr'] is not None else None,
        f"{row['hops']} hop{'s' if row['hops'] != 1 else ''}" if row['hops'] is not None else None,
        f"{row['latitude']:.4f}, {row['longitude']:.4f}" if row['latitude'] is not None and row['longitude'] is not None else None,
    ) if value]
    lines = [f"{name}  {row['id']}", '    ' + ' — '.join(details)]
    if extras:
        lines.append('    ' + ' · '.join(extras))
    return '\n'.join(lines)
