"""Sent/failed ticks: radio routing replies reach the right message and survive restarts."""
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import sitdown_desktop as desktop
from sitdown_chat import CONFIRM_TIMEOUT, ChatLog, format_message, routing_status, sent_event, status_label
from sitdown_control import handle
from sitdown_utilities import LocalUtilities


def service(packet_id=4242):
    svc = LocalUtilities.__new__(LocalUtilities)
    svc.lock, svc.config = threading.RLock(), None
    ch = lambda name: SimpleNamespace(role=1, settings=SimpleNamespace(name=name))
    svc.interface = Mock(localNode=SimpleNamespace(channels=[ch(''), ch('CentralNJ')]),
                         myInfo=SimpleNamespace(my_node_num=0xbeef))
    svc.interface.sendData.return_value = SimpleNamespace(id=packet_id)
    return svc


def reply(reason=None):
    routing = {} if reason is None else {'errorReason': reason}
    return {'decoded': {'portnum': 'ROUTING_APP', 'routing': routing}}


class RoutingTests(unittest.TestCase):
    def test_ack_and_nak_mapping(self):
        self.assertEqual(routing_status(reply()), ('relayed', 'NONE'))
        self.assertEqual(routing_status(reply('NONE')), ('relayed', 'NONE'))
        self.assertEqual(routing_status(reply('MAX_RETRANSMIT')), ('failed', 'MAX_RETRANSMIT'))
        self.assertEqual(routing_status({}), ('relayed', 'NONE'))

    def test_server_reports_status_for_its_packet(self):
        svc, seen = service(), []
        svc.chat_send(1, 'hello', on_status=seen.append)
        on_response = svc.interface.sendData.call_args.kwargs['onResponse']
        on_response(reply('MAX_RETRANSMIT'))
        self.assertEqual(seen, [{'type': 'chat_status', 'id': 4242, 'status': 'failed', 'reason': 'MAX_RETRANSMIT'}])

    def test_control_action_wires_emit_as_status_callback(self):
        svc = service()
        result = handle(svc, {'action': 'chat', 'channel': 1, 'text': 'hi'})
        self.assertEqual((result['chat']['id'], result['chat']['status']), (4242, 'sending'))
        self.assertIsNotNone(svc.interface.sendData.call_args.kwargs['onResponse'])

    def test_no_packet_id_means_unconfirmed(self):
        svc = service(packet_id=None)
        self.assertEqual(svc.chat_send(1, 'x')['status'], 'unconfirmed')


class LabelTests(unittest.TestCase):
    def test_labels(self):
        sent = sent_event(1, 'hi', '!0000beef', packet_id=7, clock=lambda: 1000)
        self.assertEqual(status_label(sent, now=1010), '… sending')
        self.assertEqual(status_label(sent, now=1000 + CONFIRM_TIMEOUT + 1), '? no confirmation')
        self.assertEqual(status_label({**sent, 'status': 'relayed'}), '✓ relayed')
        self.assertEqual(status_label({**sent, 'status': 'failed', 'reason': 'MAX_RETRANSMIT'}),
                         '✗ not relayed (MAX_RETRANSMIT)')
        self.assertIn('You  ✓ relayed', format_message({**sent, 'status': 'relayed'}))


class LogStatusTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / 'chat-history.json'

    def tearDown(self):
        self.dir.cleanup()

    def test_status_updates_only_the_matching_own_message_and_persists(self):
        log = ChatLog(path=self.path)
        log.add(sent_event(1, 'first', '!0000beef', packet_id=11))
        log.add(sent_event(1, 'second', '!0000beef', packet_id=22))
        log.add({'type': 'chat', 'channel': 1, 'from': '!1', 'text': 'theirs', 'id': 22})
        self.assertTrue(log.set_status(22, 'relayed', 'NONE'))
        self.assertFalse(log.set_status(99, 'relayed'))
        self.assertFalse(log.set_status(22, 'bogus'))
        rows = ChatLog(path=self.path).messages(1)
        self.assertEqual([(r['text'], r.get('status')) for r in rows],
                         [('first', 'unconfirmed'), ('second', 'relayed'), ('theirs', None)])

    def test_pending_message_becomes_unconfirmed_after_restart(self):
        ChatLog(path=self.path).add(sent_event(1, 'pending', '!0000beef', packet_id=5))
        self.assertEqual(ChatLog(path=self.path).messages(1)[0]['status'], 'unconfirmed')


class DesktopStatusTests(unittest.TestCase):
    def poll_with(self, status, channels=()):
        app = desktop.SitdownApp.__new__(desktop.SitdownApp)
        app.chat_log = ChatLog()
        app.chat_log.add(sent_event(2, 'mic check 1-2- 1-2', '!0000beef', packet_id=7))
        app.supervisor = Mock(poll_events=Mock(return_value=[('control', status)]), running=False, stopping=True)
        for name in ('status', 'radio_activity', 'link_warning', 'traffic', 'uptime', 'node_count'):
            setattr(app, name, Mock())
        app.snapshot = {'mqtt': {'status': 'Disabled'}, 'channels': list(channels)}
        app.restart_at = None
        app.preflight_results = Mock(get_nowait=Mock(side_effect=desktop.queue.Empty))
        app.dual_mqtt, app.wire_views, app.chat_views = None, {}, {}
        app.update_centralnj_status = Mock()
        app.auto_restart = Mock(get=Mock(return_value=False))
        app.demo = Mock(get=Mock(return_value=False))
        app.root = Mock(winfo_exists=Mock(return_value=False))
        app.show_info, app.log = Mock(), Mock()
        app.poll()
        return app

    def test_poll_applies_status_and_logs_relay(self):
        app = self.poll_with({'type': 'chat_status', 'id': 7, 'status': 'relayed', 'reason': 'NONE'},
                             channels=[{'index': 2, 'name': 'CentralNJ'}])
        self.assertEqual(app.chat_log.messages(2)[0]['status'], 'relayed')
        app.log.assert_called_once_with('Chat CentralNJ: ✓ relayed — "mic check 1-2- 1-2"')
        app.show_info.assert_not_called()

    def test_failure_is_logged_with_reason(self):
        app = self.poll_with({'type': 'chat_status', 'id': 7, 'status': 'failed', 'reason': 'MAX_RETRANSMIT'})
        app.log.assert_called_once_with('Chat Channel 2: ✗ not relayed (MAX_RETRANSMIT) — "mic check 1-2- 1-2"')

    def test_reply_for_unknown_message_logs_nothing(self):
        app = self.poll_with({'type': 'chat_status', 'id': 999, 'status': 'relayed'})
        app.log.assert_not_called()
        self.assertEqual(app.chat_log.messages(2)[0]['status'], 'sending')


if __name__ == '__main__':
    unittest.main()
