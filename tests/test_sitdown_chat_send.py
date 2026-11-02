"""Chat sending: rate limit, server send path, control action, desktop gates."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import sitdown_desktop as desktop
from sitdown_chat import BROADCAST, ChatLog, ChatSendLimiter
from sitdown_control import handle
from sitdown_utilities import LocalUtilities


class Clock:
    def __init__(self):
        self.now = 1000.0
    def __call__(self):
        return self.now


class LimiterTests(unittest.TestCase):
    def test_gap_and_per_minute_limits(self):
        clock = Clock()
        limiter = ChatSendLimiter(min_gap=2, per_minute=3, clock=clock)
        self.assertTrue(limiter.reserve())
        self.assertFalse(limiter.reserve())          # under 2 s
        for _ in range(2):
            clock.now += 2.5
            self.assertTrue(limiter.reserve())
        clock.now += 2.5
        self.assertFalse(limiter.reserve())          # 4th within a minute
        clock.now += 60
        self.assertTrue(limiter.reserve())           # window moved on


def service():
    svc = LocalUtilities.__new__(LocalUtilities)
    import threading
    svc.lock = threading.RLock()
    svc.config = None
    channel = lambda name: SimpleNamespace(role=1, settings=SimpleNamespace(name=name))
    svc.interface = Mock(localNode=SimpleNamespace(channels=[channel(''), channel('CentralNJ')]),
                         myInfo=SimpleNamespace(my_node_num=0x0000beef))
    svc.interface.sendData.return_value = SimpleNamespace(id=4242)
    return svc


class ServerSendTests(unittest.TestCase):
    def test_sends_broadcast_on_channel_and_returns_echo(self):
        svc = service()
        event = svc.chat_send(1, 'Ayyy CentralNJ')
        args, kwargs = svc.interface.sendData.call_args
        self.assertEqual(args, ('Ayyy CentralNJ'.encode(),))
        self.assertEqual({k: kwargs[k] for k in ('destinationId', 'channelIndex', 'wantAck', 'onResponseAckPermitted')},
                         {'destinationId': BROADCAST, 'channelIndex': 1, 'wantAck': True, 'onResponseAckPermitted': True})
        self.assertEqual(kwargs['portNum'], 1)       # TEXT_MESSAGE_APP
        self.assertEqual((event['channel'], event['from'], event['mine'], event['text'], event['id'], event['status']),
                         (1, '!0000beef', True, 'Ayyy CentralNJ', 4242, 'sending'))

    def test_rejects_bad_channel_empty_and_oversized_text(self):
        svc = service()
        for channel, text in ((5, 'hi'), ('1', 'hi'), (1, '   '), (1, 'x' * 201)):
            with self.subTest(channel=channel, text=text[:5]):
                with self.assertRaises(ValueError):
                    svc.chat_send(channel, text)
        svc.interface.sendData.assert_not_called()

    def test_rate_limit_blocks_rapid_second_send(self):
        svc = service()
        svc.chat_send(0, 'one')
        with self.assertRaisesRegex(ValueError, 'Slow down'):
            svc.chat_send(0, 'two')
        self.assertEqual(svc.interface.sendData.call_count, 1)

    def test_control_action_returns_result_with_echo_or_error(self):
        svc = service()
        ok = handle(svc, {'action': 'chat', 'channel': 1, 'text': 'hello'})
        self.assertEqual((ok['type'], ok['action'], ok['chat']['text']), ('result', 'chat', 'hello'))
        bad = handle(svc, {'action': 'chat', 'channel': 9, 'text': 'hello'})
        self.assertIn('unavailable', bad['error'])


class Draft:
    def __init__(self, text):
        self.text = text
    def get(self):
        return self.text
    def set(self, value):
        self.text = value


def app(enabled=True, demo=False, link='sdk_connected', running=True):
    a = desktop.SitdownApp.__new__(desktop.SitdownApp)
    a.centralnj_send_enabled = Mock(get=Mock(return_value=enabled))
    a.demo = Mock(get=Mock(return_value=demo))
    a.supervisor = Mock(running=running, link_state=link)
    a.chat_confirmed = set()
    a.show_info = Mock()
    a.control_request = Mock()
    a.root = None
    return a


class DesktopGateTests(unittest.TestCase):
    def test_each_gate_blocks_without_sending(self):
        for kwargs, reason in (({'enabled': False}, 'Transmission opt-in disabled'),
                               ({'demo': True}, 'Demo mode: transmissions disabled'),
                               ({'link': 'disconnected'}, 'Radio connection not verified'),
                               ({'running': False}, 'Radio connection not verified')):
            with self.subTest(**kwargs):
                a = app(**kwargs)
                with patch.object(desktop.messagebox, 'askyesno') as ask:
                    self.assertFalse(a.send_chat(1, 'CentralNJ', Draft('hi')))
                ask.assert_not_called()
                a.control_request.assert_not_called()
                a.show_info.assert_called_once_with('Chat not sent', reason)

    def test_empty_and_oversized_messages_are_refused(self):
        for text in ('   ', 'x' * 201):
            a = app()
            self.assertFalse(a.send_chat(1, 'CentralNJ', Draft(text)))
            a.control_request.assert_not_called()

    def test_confirmation_asked_once_per_channel_and_decline_sends_nothing(self):
        a = app()
        with patch.object(desktop.messagebox, 'askyesno', return_value=False):
            self.assertFalse(a.send_chat(1, 'CentralNJ', Draft('hi')))
        a.control_request.assert_not_called()
        with patch.object(desktop.messagebox, 'askyesno', return_value=True) as ask:
            draft = Draft('Ayyy')
            self.assertTrue(a.send_chat(1, 'CentralNJ', draft))
            self.assertTrue(a.send_chat(1, 'CentralNJ', Draft('again')))
            self.assertTrue(a.send_chat(0, 'Primary', Draft('other channel')))
        self.assertEqual(ask.call_count, 2)
        self.assertEqual(draft.text, '')
        a.control_request.assert_any_call('chat', channel=1, text='Ayyy')
        a.control_request.assert_any_call('chat', channel=0, text='other channel')


class DesktopResultTests(unittest.TestCase):
    def poll_with(self, value):
        a = desktop.SitdownApp.__new__(desktop.SitdownApp)
        a.supervisor = Mock(poll_events=Mock(return_value=[('control', value)]), running=False, stopping=True)
        for name in ('status', 'radio_activity', 'link_warning', 'traffic', 'uptime', 'node_count'):
            setattr(a, name, Mock())
        a.snapshot = {'mqtt': {'status': 'Disabled'}}
        a.restart_at = None
        a.preflight_results = Mock(get_nowait=Mock(side_effect=desktop.queue.Empty))
        a.dual_mqtt, a.wire_views, a.chat_views = None, {}, {}
        a.chat_log = ChatLog()
        a.update_centralnj_status = Mock()
        a.auto_restart = Mock(get=Mock(return_value=False))
        a.demo = Mock(get=Mock(return_value=False))
        a.root = Mock(winfo_exists=Mock(return_value=False))
        a.show_info, a.log = Mock(), Mock()
        a.poll()
        return a

    def test_sent_message_is_echoed_without_popup(self):
        echo = {'type': 'chat', 'channel': 1, 'from': '!0000beef', 'short': '', 'long': '',
                'text': 'Ayyy', 'rx_time': 1792626000, 'mine': True}
        a = self.poll_with({'type': 'result', 'action': 'chat', 'text': 'submitted', 'chat': echo})
        self.assertEqual(a.chat_log.messages(1)[0]['text'], 'Ayyy')
        a.show_info.assert_not_called()

    def test_send_error_is_shown(self):
        a = self.poll_with({'type': 'result', 'action': 'chat', 'error': 'Slow down'})
        a.show_info.assert_called_once_with('Chat not sent', 'Slow down')
        self.assertEqual(a.chat_log.channels(), [])


if __name__ == '__main__':
    unittest.main()
