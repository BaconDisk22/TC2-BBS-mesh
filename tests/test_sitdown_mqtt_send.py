"""Operator MQTT sending: gates, packet format, echo ticks, and the one publishing path."""
import base64
import configparser
import pathlib
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from meshtastic.protobuf import mqtt_pb2

import sitdown_desktop as desktop
from sitdown_chat import ChatLog, status_label
from sitdown_mqtt_crypto import channel_hash
from sitdown_mqtt_feed import MeshFeed
from sitdown_mqtt_pipeline import MQTTMessagePipeline
from sitdown_mqtt_sender import MQTTSender, build_envelope, publish_topic, send_rooms

KEY = bytes(range(16))
PSK = base64.b64encode(KEY).decode()
ME = 0x934cca70


def config(**overrides):
    values = {'enabled': 'true', 'allow_publish': 'true', 'topic': 'msh/US/2/e/CentralNJ/+',
              'channel_keys': f'CentralNJ={PSK}', 'send_as': ''}
    values.update(overrides)
    parsed = configparser.ConfigParser(interpolation=None)
    parsed.read_dict({'mqtt_centralnj': values})
    return parsed


def client(connected=True, rc=0):
    return Mock(is_connected=Mock(return_value=connected), publish=Mock(return_value=SimpleNamespace(rc=rc)))


class TopicTests(unittest.TestCase):
    def test_publish_topic_reuses_the_subscribed_channel_topic(self):
        topics = 'msh/US/2/e/LongFast/+, msh/US/2/e/CentralNJ/+'
        self.assertEqual(publish_topic(topics, 'CentralNJ', '!934cca70'), 'msh/US/2/e/CentralNJ/!934cca70')
        self.assertIsNone(publish_topic(topics, 'Other', '!934cca70'))
        self.assertIsNone(publish_topic('msh/US/2/e/CentralNJ/!0badcafe', 'CentralNJ', '!934cca70'))

    def test_send_rooms_need_topic_and_key(self):
        self.assertEqual(send_rooms('msh/US/2/e/LongFast/+, msh/US/2/e/CentralNJ/+', {'CentralNJ': KEY}),
                         ['CentralNJ'])


class EnvelopeTests(unittest.TestCase):
    def test_round_trips_through_the_receive_pipeline(self):
        payload = build_envelope('Ayyy from the desk', 'CentralNJ', KEY, ME, 4321)
        envelope = mqtt_pb2.ServiceEnvelope.FromString(payload)
        packet = envelope.packet
        self.assertEqual((envelope.channel_id, envelope.gateway_id), ('CentralNJ', '!934cca70'))
        self.assertEqual((getattr(packet, 'from'), packet.to, packet.id, packet.hop_limit),
                         (ME, 0xFFFFFFFF, 4321, 3))
        self.assertEqual(packet.channel, channel_hash('CentralNJ', KEY))
        self.assertNotIn(b'Ayyy', payload)                      # encrypted on the wire
        result = MQTTMessagePipeline(keys={'centralnj': {'CentralNJ': KEY}}).process('centralnj', payload)
        self.assertEqual(result.text, 'Ayyy from the desk')


class SenderGateTests(unittest.TestCase):
    def send(self, cfg=None, **kwargs):
        args = {'client': client(), 'sender': '!934cca70', 'session_confirmed': True}
        args.update(kwargs)
        return MQTTSender(new_id=lambda: 77).send(cfg or config(), 'centralnj', 'CentralNJ', 'hi', **args)

    def test_publishes_once_with_qos1_on_the_channel_topic(self):
        conn = client()
        self.assertEqual(self.send(client=conn), (77, '!934cca70'))
        topic, payload = conn.publish.call_args.args
        self.assertEqual(topic, 'msh/US/2/e/CentralNJ/!934cca70')
        self.assertEqual(conn.publish.call_args.kwargs, {'qos': 1})
        self.assertEqual(mqtt_pb2.ServiceEnvelope.FromString(payload).packet.id, 77)

    def test_every_gate_blocks_without_publishing(self):
        cases = {
            'publish off': (config(allow_publish='false'), {}, 'publishing is disabled'),
            'network off': (config(enabled='false'), {}, 'disabled'),
            'demo': (config(), {'demo': True}, 'Demo'),
            'not confirmed': (config(), {'session_confirmed': False}, 'confirmation'),
            'no key': (config(channel_keys=''), {}, 'No valid key'),
            'no topic': (config(topic='msh/US/2/e/LongFast/+'), {}, 'Subscribe'),
            'no sender': (config(), {'sender': None}, 'sending node'),
            'disconnected': (config(), {'client': client(connected=False)}, 'not connected'),
            'no client': (config(), {'client': None}, 'not connected'),
            'refused': (config(), {'client': client(rc=4)}, 'refused'),
        }
        for name, (cfg, kwargs, message) in cases.items():
            with self.subTest(name):
                conn = kwargs.get('client', client())
                kwargs = {**kwargs, 'client': conn} if 'client' in kwargs else {**kwargs, 'client': conn}
                with self.assertRaisesRegex(ValueError, message):
                    self.send(cfg, **kwargs)
                if name != 'refused' and conn is not None:
                    conn.publish.assert_not_called()

    def test_text_limits_and_rate_limit(self):
        sender = MQTTSender(new_id=lambda: 5)
        for text in ('  ', 'x' * 201):
            with self.assertRaisesRegex(ValueError, 'UTF-8 bytes'):
                sender.send(config(), 'centralnj', 'CentralNJ', text, client=client(), sender='!934cca70',
                            session_confirmed=True)
        sender.send(config(), 'centralnj', 'CentralNJ', 'one', client=client(), sender='!934cca70',
                    session_confirmed=True)
        with self.assertRaisesRegex(ValueError, 'Slow down'):
            sender.send(config(), 'centralnj', 'CentralNJ', 'two', client=client(), sender='!934cca70',
                        session_confirmed=True)

    def test_send_as_overrides_radio_id(self):
        conn = client()
        _id, sender = self.send(config(send_as='!0000abcd'), client=conn)
        self.assertEqual(sender, '!0000abcd')
        self.assertTrue(conn.publish.call_args.args[0].endswith('/!0000abcd'))


class OnlyPublisherTests(unittest.TestCase):
    def test_only_the_sender_module_publishes(self):
        root = pathlib.Path(__file__).resolve().parent.parent
        publishers = sorted(path.name for path in root.glob('*.py') if '.publish(' in path.read_text(encoding='utf-8'))
        self.assertEqual(publishers, ['sitdown_mqtt_sender.py'])


def app():
    a = desktop.SitdownApp.__new__(desktop.SitdownApp)
    a.demo = Mock(get=Mock(return_value=False))
    a.mqtt_sender = MQTTSender(new_id=lambda: 99)
    a.mqtt_confirmed, a.mqtt_pending = set(), {}
    a.chat_log = ChatLog()
    a.snapshot = {'me': '!934cca70'}
    conn = client()
    a.dual_mqtt = Mock(monitors={'centralnj': SimpleNamespace(client=conn)})
    a.read_config = Mock(return_value=config())
    a.show_info, a.log, a.root = Mock(), Mock(), None
    return a, conn


class Draft:
    def __init__(self, text):
        self.text = text

    def get(self):
        return self.text

    def set(self, value):
        self.text = value


class DesktopSendTests(unittest.TestCase):
    def test_confirmation_once_then_publish_and_echo_marks_on_broker(self):
        a, conn = app()
        with patch.object(desktop.messagebox, 'askyesno', return_value=False):
            self.assertFalse(a.send_mqtt('mqtt:centralnj:CentralNJ', '🌐 CentralNJ', Draft('hi')))
        conn.publish.assert_not_called()
        with patch.object(desktop.messagebox, 'askyesno', return_value=True) as ask:
            self.assertTrue(a.send_mqtt('mqtt:centralnj:CentralNJ', '🌐 CentralNJ', Draft('hi all')))
        ask.assert_called_once()
        sent = a.chat_log.messages('mqtt:centralnj:CentralNJ')[0]
        self.assertEqual((sent['mine'], sent['status'], sent['id']), (True, 'sending', 99))
        # The broker echoes our packet back on the subscribed topic.
        payload = conn.publish.call_args.args[1]
        result = MQTTMessagePipeline(keys={'centralnj': {'CentralNJ': KEY}}).process('centralnj', payload)
        a.wire_views, a.mesh_feed = {}, MeshFeed()
        a.dual_mqtt.drain_events = Mock(return_value=[('centralnj', result)])
        a.chat_views, a.chat_tabs, a.chat_tab_frames, a.chat_unread, a.nav_buttons = {}, None, {}, desktop.Counter(), []
        a.drain_mqtt_events()
        rows = a.chat_log.messages('mqtt:centralnj:CentralNJ')
        self.assertEqual(len(rows), 1)                            # not shown twice
        self.assertEqual(status_label(rows[0]), '✓ on broker')
        self.assertEqual(a.mqtt_pending, {})

    def test_gate_failure_is_shown_and_nothing_published(self):
        a, conn = app()
        a.read_config.return_value = config(allow_publish='false')
        with patch.object(desktop.messagebox, 'askyesno', return_value=True):
            self.assertFalse(a.send_mqtt('mqtt:centralnj:CentralNJ', '🌐 CentralNJ', Draft('hi')))
        conn.publish.assert_not_called()
        self.assertIn('disabled', a.show_info.call_args.args[1])
        self.assertEqual(a.chat_log.messages('mqtt:centralnj:CentralNJ'), [])

    def test_demo_mode_never_asks_or_sends(self):
        a, conn = app()
        a.demo.get.return_value = True
        with patch.object(desktop.messagebox, 'askyesno') as ask:
            self.assertFalse(a.send_mqtt('mqtt:centralnj:CentralNJ', '🌐 CentralNJ', Draft('hi')))
        ask.assert_not_called()
        conn.publish.assert_not_called()

    def test_sendable_rooms_follow_settings(self):
        a, _conn = app()
        self.assertEqual(a.sendable_rooms(), ['mqtt:centralnj:CentralNJ'])
        a.read_config.return_value = config(allow_publish='false')
        self.assertEqual(a.sendable_rooms(), [])


if __name__ == '__main__':
    unittest.main()
