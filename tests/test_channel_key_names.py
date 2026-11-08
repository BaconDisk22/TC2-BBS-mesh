"""Channel keys match topics regardless of capitalisation; saving can apply settings at once."""
import base64
import configparser
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import sitdown_desktop as desktop
from sitdown_dual_mqtt import channel_keys
from sitdown_mqtt_crypto import keys_for_topics, topic_channels, unmatched_key_names
from sitdown_mqtt_sender import MQTTSender

KEY = bytes(range(16))
PSK = base64.b64encode(KEY).decode()
TOPICS = 'msh/US/2/e/CentralNJ/+, msh/US/2/e/LongFast/!0badcafe'


class MatchingTests(unittest.TestCase):
    def test_topic_channels_keep_their_spelling(self):
        self.assertEqual(topic_channels(TOPICS), ['CentralNJ', 'LongFast'])
        self.assertEqual(topic_channels('msh/US/#, bad'), [])

    def test_key_names_take_the_topic_spelling(self):
        keys = keys_for_topics(f'CENTRALNJ={PSK}, longfast=AQ==', TOPICS)
        self.assertEqual(sorted(keys), ['CentralNJ', 'LongFast'])
        self.assertEqual(keys['CentralNJ'], KEY)
        self.assertEqual(keys_for_topics(f'Other={PSK}', TOPICS), {'Other': KEY})       # left alone
        self.assertEqual(unmatched_key_names(f'CENTRALNJ={PSK}, Other={PSK}', TOPICS), ['Other'])

    def test_monitors_and_sender_use_the_matched_name(self):
        config = configparser.ConfigParser(interpolation=None)
        config.read_dict({'mqtt_centralnj': {'enabled': 'true', 'allow_publish': 'true',
                                             'topic': 'msh/US/2/e/CentralNJ/+', 'channel_keys': f'CENTRALNJ={PSK}'}})
        self.assertEqual(channel_keys(config)['centralnj'], {'CentralNJ': KEY})
        client = Mock(is_connected=Mock(return_value=True), publish=Mock(return_value=SimpleNamespace(rc=0)))
        MQTTSender(new_id=lambda: 5).send(config, 'centralnj', 'CentralNJ', 'hi', client=client,
                                          sender='!934cca70', session_confirmed=True)
        client.publish.assert_called_once()


def app(running=False, live=()):
    a = desktop.SitdownApp.__new__(desktop.SitdownApp)
    a.supervisor = Mock(running=running) if running else None
    monitors = {n: SimpleNamespace(client=object() if n in live else None) for n in ('centralnj', 'sjmesh')}
    a.dual_mqtt = Mock(monitors=monitors, pipeline=SimpleNamespace(keys={})) if live or running else None
    a.demo = Mock(get=Mock(return_value=False))
    a.root = None
    a.show_info, a.log, a.restart, a.refresh_mqtt_states = Mock(), Mock(), Mock(), Mock()
    return a


def saved(keys=f'CENTRALNJ={PSK}'):
    config = configparser.ConfigParser(interpolation=None)
    config.read_dict({'mqtt_centralnj': {'topic': 'msh/US/2/e/CentralNJ/+', 'channel_keys': keys}})
    return config


class SaveTests(unittest.TestCase):
    def test_idle_app_just_reports_and_warns_about_stray_keys(self):
        a = app()
        a.settings_saved(saved(f'Wrong={PSK}'))
        title, message = a.show_info.call_args.args
        self.assertEqual(title, 'Settings saved')
        self.assertIn("the key for Wrong matches none of your topics", message)
        self.assertNotIn(PSK, message)

    def test_live_app_offers_apply_and_restarts_and_reconnects(self):
        a = app(running=True, live=('centralnj',))
        old = a.dual_mqtt
        with patch.object(desktop.messagebox, 'askyesno', return_value=True) as ask, \
             patch('sitdown_dual_mqtt.DualMQTTMonitors') as factory:
            a.settings_saved(saved())
        self.assertIn('restarts the BBS server and reconnects CentralNJ', ask.call_args.args[1])
        self.assertEqual(old.pipeline.keys['centralnj'], {'CentralNJ': KEY})        # keys live before reconnect
        old.stop.assert_called_once()
        factory.return_value.connect.assert_called_once_with('centralnj')
        a.restart.assert_called_once()

    def test_declining_applies_nothing(self):
        a = app(running=True)
        with patch.object(desktop.messagebox, 'askyesno', return_value=False):
            a.settings_saved(saved())
        a.restart.assert_not_called()


if __name__ == '__main__':
    unittest.main()
