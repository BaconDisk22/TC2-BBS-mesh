"""Reading MQTT channel text with operator-supplied keys."""
import base64
import configparser
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from meshtastic.protobuf import mesh_pb2, mqtt_pb2

import sitdown_desktop as desktop
from sitdown_chat import ChatLog, clean_event, event_key, mqtt_room
from sitdown_dual_mqtt import DualMQTTMonitors, channel_keys
from sitdown_mqtt_crypto import (DEFAULT_KEY, channel_hash, crypt, nonce, open_packet, parse_channel_keys,
                                 parse_psk, safe_channel_keys)
from sitdown_mqtt_feed import MeshFeed, format_entry
from sitdown_mqtt_pipeline import MQTTMessagePipeline

KEY = bytes(range(16))
PSK = base64.b64encode(KEY).decode()


def data(portnum, payload):
    out = mesh_pb2.Data()
    out.portnum = portnum
    out.payload = payload
    return out.SerializeToString()


def envelope(plain, channel='CentralNJ', key=KEY, packet_id=0x1234, sender=0xb2c649cb, to=0xFFFFFFFF,
             encrypt=True, hash_byte=None):
    env = mqtt_pb2.ServiceEnvelope()
    env.channel_id, env.gateway_id = channel, '!0badcafe'
    packet = env.packet
    packet.id, packet.to = packet_id, to
    setattr(packet, 'from', sender)
    if encrypt:
        packet.channel = channel_hash(channel, key) if hash_byte is None else hash_byte
        packet.encrypted = crypt(key, packet_id, sender, plain)
    else:
        packet.decoded.ParseFromString(plain)
    return env.SerializeToString()


class CryptoTests(unittest.TestCase):
    def test_psk_shorthand_default_and_custom_keys(self):
        self.assertEqual(parse_psk('AQ=='), DEFAULT_KEY)
        self.assertEqual(parse_psk('Ag==')[-1], DEFAULT_KEY[-1] + 1)
        self.assertEqual(parse_psk(PSK), KEY)
        self.assertEqual(len(parse_psk(base64.b64encode(bytes(32)).decode())), 32)
        for bad in ('AA==', 'not base64!', base64.b64encode(bytes(8)).decode(), 'Cw=='):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    parse_psk(bad)

    def test_channel_key_setting(self):
        keys = parse_channel_keys(f'CentralNJ={PSK}, LongFast=AQ==')
        self.assertEqual(keys, {'CentralNJ': KEY, 'LongFast': DEFAULT_KEY})
        self.assertEqual(parse_channel_keys(''), {})
        for bad in ('CentralNJ', '=AQ==', 'Bad Name=AQ==', 'A=AQ==,A=AQ==', 'TooLongName12=AQ==', 'X=zz'):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    parse_channel_keys(bad)
        self.assertEqual(safe_channel_keys('X=zz'), {})

    def test_errors_never_echo_the_key(self):
        secret = base64.b64encode(bytes(range(8))).decode()
        with self.assertRaises(ValueError) as caught:
            parse_channel_keys(f'CentralNJ={secret}')
        self.assertNotIn(secret, str(caught.exception))

    def test_meshtastic_nonce_layout_and_known_vector(self):
        self.assertEqual(nonce(0x01020304, 0xaabbccdd).hex(), '0403020100000000ddccbbaa00000000')
        # Frozen vector; the same code opened 144/144 real LongFast packets on mqtt.meshtastic.org.
        self.assertEqual(crypt(DEFAULT_KEY, 1, 2, b'hello').hex(), '7582cac9d4')
        sealed = crypt(DEFAULT_KEY, 1, 2, data(1, b'hi'))
        self.assertEqual(crypt(DEFAULT_KEY, 1, 2, sealed), data(1, b'hi'))
        self.assertEqual(channel_hash('LongFast', DEFAULT_KEY), 8)

    def test_wrong_key_does_not_open(self):
        sealed = crypt(KEY, 9, 9, data(1, b'secret'))
        self.assertEqual(open_packet(KEY, 9, 9, sealed).payload, b'secret')
        self.assertIsNone(open_packet(DEFAULT_KEY, 9, 9, sealed))
        self.assertIsNone(open_packet(KEY, 9, 9, b''))


class PipelineTests(unittest.TestCase):
    def pipeline(self):
        return MQTTMessagePipeline(keys={'centralnj': {'CentralNJ': KEY}})

    def test_text_on_keyed_channel_is_read(self):
        result = self.pipeline().process('centralnj', envelope(data(1, 'Ayyy é'.encode())))
        self.assertEqual((result.accepted, result.reason, result.text), (True, 'Channel text', 'Ayyy é'))
        self.assertTrue(result.summary.decrypted)
        self.assertEqual(result.summary.portnum, 1)

    def test_no_key_wrong_network_wrong_hash_or_dm_stays_metadata_only(self):
        pipe = self.pipeline()
        cases = {
            'other network': ('sjmesh', envelope(data(1, b'x'), packet_id=1)),
            'unkeyed channel': ('centralnj', envelope(data(1, b'x'), channel='Other', packet_id=2)),
            'other key same name': ('centralnj', envelope(data(1, b'x'), key=DEFAULT_KEY, packet_id=3)),
            'hash mismatch': ('centralnj', envelope(data(1, b'x'), hash_byte=0, packet_id=4)),
        }
        for name, (network, payload) in cases.items():
            with self.subTest(name):
                result = pipe.process(network, payload)
                self.assertTrue(result.accepted)
                self.assertIsNone(result.text)
        direct = pipe.process('centralnj', envelope(data(1, b'x'), to=0x934cca70, packet_id=5))
        self.assertIsNone(direct.text)
        self.assertTrue(direct.summary.decrypted)

    def test_nodeinfo_names_the_sender_of_later_text(self):
        pipe = self.pipeline()
        user = mesh_pb2.User(short_name='EDC', long_name='Every Day Carry')
        pipe.process('centralnj', envelope(data(4, user.SerializeToString()), packet_id=10))
        result = pipe.process('centralnj', envelope(data(1, b'hi'), packet_id=11))
        self.assertEqual((result.short, result.long), ('EDC', 'Every Day Carry'))

    def test_unencrypted_text_only_on_keyed_channel(self):
        pipe = self.pipeline()
        self.assertEqual(pipe.process('centralnj', envelope(data(1, b'plain'), encrypt=False)).text, 'plain')
        self.assertIsNone(pipe.process('centralnj', envelope(data(1, b'plain'), channel='Other',
                                                             encrypt=False, packet_id=2)).text)

    def test_duplicates_are_not_read_twice(self):
        pipe = self.pipeline()
        payload = envelope(data(1, b'once'))
        self.assertEqual(pipe.process('centralnj', payload).text, 'once')
        self.assertFalse(pipe.process('centralnj', payload).accepted)

    def test_feed_line_never_shows_text(self):
        result = self.pipeline().process('centralnj', envelope(data(1, b'hidden words')))
        feed = MeshFeed(clock=lambda: 0)
        feed.add('centralnj', result)
        line = format_entry(feed.entries('centralnj')[0])
        self.assertIn('shown in CHAT', line)
        self.assertNotIn('hidden', line)

    def test_monitors_build_keys_from_each_profile(self):
        config = configparser.ConfigParser()
        config.read_dict({'mqtt_centralnj': {'channel_keys': f'CentralNJ={PSK}'},
                          'mqtt_sjmesh': {'channel_keys': 'broken'}})
        self.assertEqual(channel_keys(config), {'centralnj': {'CentralNJ': KEY}, 'sjmesh': {}})
        monitors = DualMQTTMonitors(config)
        self.assertEqual(monitors.pipeline.keys['centralnj'], {'CentralNJ': KEY})


class HistoryTests(unittest.TestCase):
    def event(self, **extra):
        return {'type': 'mqtt', 'network': 'sjmesh', 'room': 'LongFast', 'from': '!0000abcd', 'short': 'AB',
                'long': '', 'text': 'hello', 'rx_time': 1792626000, 'mine': False, **extra}

    def test_rooms_are_separate_and_survive_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'chat-history.json'
            log = ChatLog(path=path)
            log.add(self.event())
            log.add(self.event(network='centralnj', room='CentralNJ'))
            again = ChatLog(path=path)
            self.assertEqual(again.rooms(), ['mqtt:centralnj:CentralNJ', 'mqtt:sjmesh:LongFast'])
            self.assertEqual(again.channels(), [])
            self.assertIn('MQTT broker', again.render('mqtt:sjmesh:LongFast', '🌐 SJMesh · LongFast'))

    def test_bad_rooms_rejected(self):
        for bad in ({'network': 'other'}, {'room': 'a/b'}, {'room': ''}, {'room': 'x' * 12}, {'room': 'a:b'}):
            with self.subTest(bad=bad):
                self.assertIsNone(clean_event(self.event(**bad)))
        self.assertEqual(mqtt_room(event_key(clean_event(self.event()))), ('sjmesh', 'LongFast'))


class DesktopTests(unittest.TestCase):
    def test_tab_names(self):
        self.assertEqual(desktop.SitdownApp.room_tab_name('mqtt:centralnj:CentralNJ'), '🌐 CentralNJ')
        self.assertEqual(desktop.SitdownApp.room_tab_name('mqtt:sjmesh:LongFast'), '🌐 SJMesh · LongFast')

    def test_drained_text_goes_to_chat_and_counts_unread(self):
        a = desktop.SitdownApp.__new__(desktop.SitdownApp)
        a.chat_log, a.chat_views, a.chat_tabs, a.chat_tab_frames = ChatLog(), {}, None, {}
        a.chat_unread, a.nav_buttons, a.wire_views = desktop.Counter(), [], {}
        a.mesh_feed = MeshFeed()
        result = MQTTMessagePipeline(keys={'centralnj': {'CentralNJ': KEY}}).process(
            'centralnj', envelope(data(1, b'from the broker')))
        a.dual_mqtt = Mock(drain_events=Mock(return_value=[('centralnj', result)]))
        a.drain_mqtt_events()
        rows = a.chat_log.messages('mqtt:centralnj:CentralNJ')
        self.assertEqual((rows[0]['text'], rows[0]['from']), ('from the broker', '!b2c649cb'))
        self.assertEqual(a.chat_unread['mqtt:centralnj:CentralNJ'], 1)


if __name__ == '__main__':
    unittest.main()
