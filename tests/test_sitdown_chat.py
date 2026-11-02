"""Offline radio channel chat: event filtering, history, safe emission, desktop routing."""
import io
import json
import os
import threading
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch

import sitdown_desktop as desktop
from sitdown_chat import BROADCAST, MAX_TEXT, ChatLog, chat_event, format_message
from sitdown_control import PREFIX, emit


def packet(text='Ayyy', to=BROADCAST, channel=None, portnum='TEXT_MESSAGE_APP', sender=0x02ed8680, **extra):
    p = {'from': sender, 'fromId': f'!{sender:08x}', 'to': to, 'rxTime': 1792616310,
         'decoded': {'portnum': portnum, 'payload': text.encode() if isinstance(text, str) else text}}
    if channel is not None:
        p['channel'] = channel
    p.update(extra)
    return p


def interface(my_num=0x0000beef):
    return SimpleNamespace(myInfo=SimpleNamespace(my_node_num=my_num),
                           nodes={'!02ed8680': {'user': {'shortName': 'XiG4', 'longName': 'Rav4 KC1TCD'}}})


class ChatEventTests(unittest.TestCase):
    def test_channel_message_becomes_event_with_names(self):
        event = chat_event(packet(channel=2), interface())
        self.assertEqual(event, {'type': 'chat', 'channel': 2, 'from': '!02ed8680', 'short': 'XiG4',
                                 'long': 'Rav4 KC1TCD', 'text': 'Ayyy', 'rx_time': 1792616310, 'mine': False})

    def test_primary_channel_is_default(self):
        self.assertEqual(chat_event(packet(), interface())['channel'], 0)

    def test_direct_messages_stay_with_the_bbs(self):
        self.assertIsNone(chat_event(packet(to=0x0000beef), interface()))

    def test_non_text_and_malformed_packets_are_ignored(self):
        self.assertIsNone(chat_event(packet(portnum='POSITION_APP'), interface()))
        self.assertIsNone(chat_event({'to': BROADCAST}, interface()))
        self.assertIsNone(chat_event(packet(channel=9), interface()))
        self.assertIsNone(chat_event(packet(channel='x'), interface()))

    def test_unknown_sender_bad_utf8_and_long_text(self):
        event = chat_event(packet(text=b'\xff' + b'a' * 900, sender=0x12345678), interface())
        self.assertEqual((event['short'], event['long']), ('', ''))
        self.assertEqual(len(event['text']), MAX_TEXT)
        self.assertTrue(event['text'].startswith('�'))

    def test_own_radio_is_marked(self):
        event = chat_event(packet(sender=0x0000beef), interface())
        self.assertTrue(event['mine'])
        self.assertIn('You', format_message(event))


class ChatLogTests(unittest.TestCase):
    def test_history_is_per_channel_bounded_and_ordered(self):
        log = ChatLog(limit=2)
        for i, channel in enumerate((0, 0, 0, 1)):
            log.add(chat_event(packet(text=f'm{i}', channel=channel), interface()))
        self.assertEqual([m['text'] for m in log.messages(0)], ['m1', 'm2'])
        self.assertEqual(log.channels(), [0, 1])

    def test_invalid_events_are_rejected(self):
        log = ChatLog()
        for bad in ({}, {'type': 'snapshot'}, {'type': 'chat', 'channel': 20, 'text': 'x', 'from': '!1'},
                    {'type': 'chat', 'channel': 0, 'text': 5, 'from': '!1'}):
            self.assertFalse(log.add(bad))
        self.assertEqual(log.channels(), [])

    def test_render_shows_sender_and_text(self):
        log = ChatLog()
        log.add(chat_event(packet(channel=1), interface()))
        text = log.render(1, 'CentralNJ')
        self.assertIn('CentralNJ — channel messages', text)
        self.assertIn('XiG4 (Rav4 KC1TCD) !02ed8680', text)
        self.assertIn('    Ayyy', text)
        self.assertIn('No messages yet', ChatLog().render(0, 'LongFast'))


class EmitTests(unittest.TestCase):
    def test_concurrent_emits_never_interleave(self):
        out = io.StringIO()
        def burst(n):
            for i in range(200):
                emit({'type': 'chat', 'text': f'{n}-{i}-' + 'x' * 200})
        with patch.dict(os.environ, {'SITDOWN_CONTROL_TOKEN': 't'}), redirect_stdout(out):
            threads = [threading.Thread(target=burst, args=(n,)) for n in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 800)
        for line in lines:
            self.assertTrue(line.startswith(PREFIX + 't '))
            json.loads(line[len(PREFIX + 't '):])


class DesktopRoutingTests(unittest.TestCase):
    def test_chat_event_is_stored_not_popped_up(self):
        app = desktop.SitdownApp.__new__(desktop.SitdownApp)
        app.chat_log = ChatLog()
        app.show_info = Mock()
        app.log = Mock()
        event = chat_event(packet(channel=1), interface())
        monitor = Mock(poll_events=Mock(return_value=[('control', event)]), running=False, stopping=True)
        app.supervisor = monitor
        for name in ('status', 'radio_activity', 'link_warning', 'traffic', 'uptime', 'node_count'):
            setattr(app, name, Mock())
        app.snapshot = {'mqtt': {'status': 'Disabled'}}
        app.restart_at = None
        app.preflight_results = Mock(get_nowait=Mock(side_effect=desktop.queue.Empty))
        app.dual_mqtt = None
        app.wire_views = {}
        app.chat_views = {}
        app.chat_unread, app.chat_tabs, app.chat_tab_frames, app.nav_buttons = desktop.Counter(), None, {}, []
        app.update_centralnj_status = Mock()
        app.auto_restart = Mock(get=Mock(return_value=False))
        app.demo = Mock(get=Mock(return_value=False))
        app.root = Mock(winfo_exists=Mock(return_value=False))
        app.poll()
        self.assertEqual(app.chat_log.messages(1)[0]['text'], 'Ayyy')
        self.assertEqual(app.chat_unread[1], 1)
        app.show_info.assert_not_called()

    def test_channel_names_come_from_snapshot_and_history(self):
        app = desktop.SitdownApp.__new__(desktop.SitdownApp)
        app.chat_log = ChatLog()
        app.snapshot = {'channels': [{'index': 0, 'name': 'LongFast'}, {'index': 1, 'name': 'CentralNJ'}]}
        app.chat_log.add(chat_event(packet(channel=3), interface()))
        self.assertEqual(app.chat_channel_names(), {0: 'LongFast', 1: 'CentralNJ', 3: 'Channel 3'})
        app.snapshot = {'channels': []}
        app.chat_log = ChatLog()
        self.assertEqual(app.chat_channel_names(), {0: 'Primary'})


if __name__ == '__main__':
    unittest.main()
