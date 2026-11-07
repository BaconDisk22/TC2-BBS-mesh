"""Design-system checks that need no window: contrast, states, navigation and settings layout."""
import unittest
from unittest.mock import Mock, patch

import sitdown_desktop as desktop
from sitdown_ui import PALETTES, STATES, TEXT_SIZES, TYPE, contrast, state_of


class PaletteTests(unittest.TestCase):
    def test_both_themes_define_the_same_roles(self):
        self.assertEqual(set(PALETTES['dark']), set(PALETTES['light']))

    def test_text_and_controls_meet_wcag_contrast(self):
        for mode, p in PALETTES.items():
            checks = [('text', 'surface', 7), ('text', 'bg', 7), ('muted', 'surface', 4.5), ('muted', 'bg', 4.5),
                      ('muted', 'sidebar', 4.5), ('subtle', 'surface', 4.5), ('subtle', 'sidebar', 4.5),
                      ('on_accent', 'accent', 4.5), ('on_accent', 'accent_hover', 4.5), ('text', 'raised', 4.5),
                      ('danger', 'raised', 4.5), ('log_text', 'log_bg', 7), ('text', 'field', 7)]
            for state in ('success', 'warning', 'danger', 'info'):
                checks.append((state, 'surface', 4.5))
            for fg, bg, minimum in checks:
                with self.subTest(mode=mode, pair=f'{fg}/{bg}'):
                    self.assertGreaterEqual(contrast(p[fg], p[bg]), minimum)

    def test_type_scale_and_sizes(self):
        self.assertGreater(TYPE['display'], TYPE['title'] > TYPE['heading'] > TYPE['small'])
        self.assertEqual(TEXT_SIZES['default'], 1.0)


class StateTests(unittest.TestCase):
    def test_status_words_map_to_states(self):
        expected = {
            'Stopped': 'idle', 'Starting...': 'busy', 'Running': 'ok', 'Demo running — no radio': 'warn',
            'Radio: not connected (server stopped)': 'idle', 'Radio connected': 'ok',
            'Radio disconnected — waiting 30s for it to come back': 'error', 'Radio: connecting…': 'busy',
            'MQTT: off': 'idle', 'MQTT: 1 of 2 networks live': 'ok', 'Connected — listening': 'ok',
            'Connection refused': 'error', 'Retry in 4s (attempt 2)': 'busy', 'Start failed': 'error',
            'Stopped — recovery cancelled': 'idle', 'Radio RX observed 4s ago': 'ok',
        }
        for text, state in expected.items():
            with self.subTest(text=text):
                self.assertEqual(state_of(text), state)
        self.assertTrue(all(state_of(t) in STATES for t in expected))


class StructureTests(unittest.TestCase):
    def test_every_page_is_in_the_sidebar_once_with_a_title(self):
        pages = [page for _group, items in desktop.NAV for page, _icon, _text in items]
        self.assertEqual(len(pages), len(set(pages)))
        self.assertEqual(set(pages), set(desktop.PAGE_INFO))
        for required in ('HOME', 'CHAT', 'NODES', 'WIRE', 'RADIO', 'MESSAGES', 'BOARDS', 'BBS', 'SETTINGS', 'ABOUT'):
            self.assertIn(required, pages)

    def test_settings_keep_every_config_key_with_readable_labels(self):
        original = {
            'startup': {'enabled', 'channel', 'message'}, 'announcements': {'admin_nodes'},
            'weather': {'internet_enabled', 'local_report', 'latitude', 'longitude'},
            'mqtt_monitor': {'enabled', 'host', 'topic', 'tls', 'port', 'username', 'password'},
            'personality': {'public_mode', 'allow_public_profanity'},
        }
        network = {'enabled', 'auto_connect', 'host', 'topic', 'tls', 'port', 'username', 'password',
                   'channel_keys', 'allow_publish', 'send_as'}
        original.update(mqtt_centralnj=network, mqtt_sjmesh=network)
        for section, keys in original.items():
            tab, entries = desktop.SETTINGS_SECTIONS[section]
            found = {key for key, *_ in entries if key != '#'}
            with self.subTest(section=section):
                self.assertEqual(found, keys)
                self.assertTrue(tab)
                for key, label, default, _help in entries:
                    self.assertTrue(label and label[0].isupper(), label)
                    self.assertIsInstance(default, str)

    def test_launcher_prefs_round_trip_theme_size_and_onboarding(self):
        import json, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'launcher.json'
            path.write_text(json.dumps({'appearance': 'light', 'text_size': 'large', 'onboarded': True}))
            app = desktop.SitdownApp.__new__(desktop.SitdownApp)
            for name in ('auto_start', 'connection', 'address', 'community', 'welcome', 'desktop_mouth',
                         'public_mouth', 'public_profanity', 'appearance', 'text_size'):
                setattr(app, name, Mock())
            app.desktop_mouth.get.return_value = 'clean'
            with patch.object(desktop, 'CONFIG', path):
                app.load()
            app.appearance.set.assert_called_with('light')
            app.text_size.set.assert_called_with('large')
            self.assertTrue(app.onboarded)


class StopConfirmationTests(unittest.TestCase):
    def app(self, running=True, demo=False):
        app = desktop.SitdownApp.__new__(desktop.SitdownApp)
        app.supervisor = Mock(running=running)
        app.demo = Mock(get=Mock(return_value=demo))
        app.root = None
        app.stop = Mock()
        return app

    def test_live_server_asks_before_stopping(self):
        app = self.app()
        with patch.object(desktop.messagebox, 'askyesno', return_value=False) as ask:
            app.confirm_stop()
        ask.assert_called_once()
        app.stop.assert_not_called()
        with patch.object(desktop.messagebox, 'askyesno', return_value=True):
            app.confirm_stop()
        app.stop.assert_called_once()

    def test_demo_or_stopped_server_stops_without_asking(self):
        for kwargs in ({'demo': True}, {'running': False}):
            app = self.app(**kwargs)
            with patch.object(desktop.messagebox, 'askyesno') as ask:
                app.confirm_stop()
            ask.assert_not_called()
            app.stop.assert_called_once()


if __name__ == '__main__':
    unittest.main()
