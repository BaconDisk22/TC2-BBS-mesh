"""Operator direct messages: conversation mode, send path, history and desktop gates."""
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import sitdown_desktop as desktop
from sitdown_chat import BROADCAST, ChatLog, clean_event, event_key, format_message, status_label
from sitdown_control import handle
from sitdown_dm import Conversations, claim_reply, dm_event, dm_status, peer_id
from sitdown_utilities import LocalUtilities

ME = 0x934cca70
PEER = '!b2c649cb'


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def packet(text, sender=0xb2c649cb, to=ME, portnum='TEXT_MESSAGE_APP'):
    return {'from': sender, 'fromId': f'!{sender:08x}', 'to': to, 'rxTime': 1792626000,
            'decoded': {'portnum': portnum, 'payload': text.encode()}}


def interface():
    return SimpleNamespace(myInfo=SimpleNamespace(my_node_num=ME),
                           nodes={PEER: {'user': {'shortName': 'EDC', 'longName': 'Every Day Carry'}}})


class PeerIdTests(unittest.TestCase):
    def test_accepts_ids_and_numbers_and_rejects_broadcast_zero_and_junk(self):
        self.assertEqual(peer_id('!B2C649CB'), PEER)
        self.assertEqual(peer_id('b2c649cb'), PEER)
        self.assertEqual(peer_id(0xb2c649cb), PEER)
        for bad in (BROADCAST, 0, '!ffffffff', '!123', 'EDC', None, True, '!b2c649cbx'):
            with self.subTest(bad=bad):
                self.assertIsNone(peer_id(bad))


class ConversationTests(unittest.TestCase):
    def test_only_open_unexpired_conversations_claim_plain_replies(self):
        clock = Clock()
        talks = Conversations(seconds=600, clock=clock)
        self.assertFalse(talks.claims(PEER, 'hi'))
        talks.open(PEER)
        self.assertTrue(talks.claims(PEER, 'thanks!'))
        self.assertFalse(talks.claims(PEER, '/menu'))           # slash commands still go to the BBS
        self.assertFalse(talks.claims(PEER, '  /mail'))
        self.assertFalse(talks.claims('!00000001', 'hi'))       # other nodes are untouched
        clock.now += 599
        self.assertTrue(talks.claims(PEER, 'still here'))        # each reply restarts the 10 minutes
        clock.now += 599
        self.assertTrue(talks.claims(PEER, 'yep'))
        clock.now += 601
        self.assertFalse(talks.claims(PEER, 'late'))
        self.assertEqual(talks.active(), [])

    def test_close_ends_conversation(self):
        talks = Conversations(clock=Clock())
        talks.open(PEER)
        self.assertTrue(talks.close(PEER))
        self.assertFalse(talks.claims(PEER, 'hi'))
        self.assertFalse(talks.close(PEER))

    def test_number_of_open_conversations_is_bounded(self):
        clock = Clock()
        talks = Conversations(clock=clock)
        for n in range(1, 40):
            clock.now += 1
            talks.open(f'!{n:08x}')
        self.assertEqual(len(talks.active()), 32)
        self.assertNotIn('!00000001', talks.active())          # oldest dropped first


class ReceiveTests(unittest.TestCase):
    def test_dm_event_only_for_text_addressed_to_this_radio(self):
        event = dm_event(packet('thanks!'), interface())
        self.assertEqual((event['type'], event['peer'], event['short'], event['text'], event['mine']),
                         ('dm', PEER, 'EDC', 'thanks!', False))
        self.assertIsNone(dm_event(packet('hi', to=BROADCAST), interface()))
        self.assertIsNone(dm_event(packet('hi', to=0x12345678), interface()))
        self.assertIsNone(dm_event(packet('hi', sender=ME), interface()))
        self.assertIsNone(dm_event(packet('hi', portnum='POSITION_APP'), interface()))
        self.assertIsNone(dm_event({'decoded': None}, interface()))
        self.assertIsNone(dm_event(packet('hi'), SimpleNamespace(myInfo=None)))

    def test_claim_reply_leaves_everything_else_to_the_bbs(self):
        talks = Conversations(clock=Clock())
        self.assertIsNone(claim_reply(packet('hello'), interface(), talks))
        talks.open(PEER)
        self.assertEqual(claim_reply(packet('hello'), interface(), talks)['text'], 'hello')
        self.assertIsNone(claim_reply(packet('/bulletins'), interface(), talks))
        self.assertIsNone(claim_reply(packet('hello', sender=0x11111111), interface(), talks))
        self.assertIsNone(claim_reply(packet('hello', to=BROADCAST), interface(), talks))


class StatusTests(unittest.TestCase):
    def test_ack_from_destination_is_delivered_implicit_is_relayed_nak_failed(self):
        ok = {'decoded': {'routing': {'errorReason': 'NONE'}}}
        self.assertEqual(dm_status({**ok, 'fromId': PEER}, PEER), ('delivered', 'NONE'))
        self.assertEqual(dm_status({**ok, 'from': 0xb2c649cb}, PEER), ('delivered', 'NONE'))
        self.assertEqual(dm_status({**ok, 'from': ME}, PEER), ('relayed', 'NONE'))
        self.assertEqual(dm_status({'decoded': {'routing': {'errorReason': 'MAX_RETRANSMIT'}}, 'fromId': PEER}, PEER),
                         ('failed', 'MAX_RETRANSMIT'))
        self.assertEqual(status_label({'status': 'delivered', 'rx_time': 0}), '✓✓ delivered')


def service():
    svc = LocalUtilities.__new__(LocalUtilities)
    svc.lock = threading.RLock()
    svc.config = None
    svc.interface = Mock(myInfo=SimpleNamespace(my_node_num=ME))
    svc.interface.sendData.return_value = SimpleNamespace(id=777)
    return svc


class SendTests(unittest.TestCase):
    def test_sends_direct_to_node_with_ack_and_opens_conversation(self):
        svc = service()
        statuses = []
        event = svc.dm_send('!B2C649CB', 'you around?', on_status=statuses.append)
        args, kwargs = svc.interface.sendData.call_args
        self.assertEqual(args, (b'you around?',))
        self.assertEqual({k: kwargs[k] for k in ('destinationId', 'wantAck', 'onResponseAckPermitted', 'channelIndex')},
                         {'destinationId': 0xb2c649cb, 'wantAck': True, 'onResponseAckPermitted': True, 'channelIndex': 0})
        self.assertEqual(kwargs['portNum'], 1)
        self.assertEqual((event['type'], event['peer'], event['mine'], event['id'], event['status']),
                         ('dm', PEER, True, 777, 'sending'))
        self.assertTrue(svc.conversations.claims(PEER, 'yes'))
        kwargs['onResponse']({'fromId': PEER, 'decoded': {'routing': {'errorReason': 'NONE'}}})
        self.assertEqual(statuses, [{'type': 'chat_status', 'id': 777, 'status': 'delivered', 'reason': 'NONE'}])

    def test_rejects_bad_node_self_and_bad_text_without_sending(self):
        svc = service()
        for node, text in (('EDC', 'hi'), ('!ffffffff', 'hi'), (f'!{ME:08x}', 'hi'),
                           (PEER, '  '), (PEER, 'x' * 201), (PEER, None)):
            with self.subTest(node=node):
                with self.assertRaises(ValueError):
                    svc.dm_send(node, text)
        svc.interface.sendData.assert_not_called()
        self.assertEqual(svc.conversations.active(), [])

    def test_shares_the_chat_rate_limit(self):
        svc = service()
        svc.dm_send(PEER, 'one')
        with self.assertRaisesRegex(ValueError, 'Slow down'):
            svc.dm_send(PEER, 'two')

    def test_control_actions(self):
        svc = service()
        sent = handle(svc, {'action': 'dm', 'node': PEER, 'text': 'hi'})
        self.assertEqual((sent['action'], sent['chat']['peer']), ('dm', PEER))
        ended = handle(svc, {'action': 'dm_end', 'node': PEER})
        self.assertIn('ended', ended['text'])
        self.assertFalse(svc.conversations.claims(PEER, 'hi'))
        self.assertIn('error', handle(svc, {'action': 'dm', 'node': 'nobody', 'text': 'hi'}))
        self.assertIn('error', handle(svc, {'action': 'dm_end', 'node': 'nobody'}))


class HistoryTests(unittest.TestCase):
    def dm(self, text, mine=False, peer=PEER, rx_time=1792626000):
        return {'type': 'dm', 'peer': peer, 'from': '!934cca70' if mine else peer, 'short': '', 'long': '',
                'text': text, 'rx_time': rx_time, 'mine': mine, 'id': 5 if mine else None,
                'status': 'sending'}

    def test_dms_are_kept_apart_from_channels_and_survive_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'chat-history.json'
            log = ChatLog(path=path)
            log.add({'type': 'chat', 'channel': 0, 'from': '!00000002', 'short': '', 'long': '',
                     'text': 'channel', 'rx_time': 1, 'mine': False})
            log.add(self.dm('ping', mine=True))
            log.add(self.dm('other', peer='!00000009', rx_time=1792627000))
            self.assertEqual(log.channels(), [0])
            self.assertEqual(log.peers(), ['!00000009', PEER])        # most recent first
            self.assertEqual(log.set_status(5, 'delivered')['status'], 'delivered')
            self.assertIsNone(log.set_status(5, 'failed'))             # delivered is final
            again = ChatLog(path=path)
            self.assertEqual([m['text'] for m in again.messages(f'dm:{PEER}')], ['ping'])
            self.assertEqual(again.messages(f'dm:{PEER}')[0]['status'], 'delivered')
            self.assertIn('direct messages', again.render(f'dm:{PEER}', '✉ EDC'))

    def test_bad_dm_rows_are_rejected(self):
        for bad in ({'peer': 'EDC'}, {'peer': '!FFFF'}, {'peer': None}, {'text': 5}):
            with self.subTest(bad=bad):
                self.assertIsNone(clean_event({**self.dm('x'), **bad}))
        self.assertEqual(event_key(clean_event(self.dm('x'))), f'dm:{PEER}')
        self.assertIn('You  ? no confirmation', format_message(clean_event(self.dm('x', mine=True))))   # old send


class Draft:
    def __init__(self, text):
        self.text = text

    def get(self):
        return self.text

    def set(self, value):
        self.text = value


def app(enabled=True, demo=False, link='sdk_connected'):
    a = desktop.SitdownApp.__new__(desktop.SitdownApp)
    a.centralnj_send_enabled = Mock(get=Mock(return_value=enabled))
    a.demo = Mock(get=Mock(return_value=demo))
    a.supervisor = Mock(running=True, link_state=link)
    a.dm_confirmed = set()
    a.show_info = Mock()
    a.control_request = Mock()
    a.root = None
    return a


class DesktopGateTests(unittest.TestCase):
    def test_gates_block_without_sending(self):
        for kwargs in ({'enabled': False}, {'demo': True}, {'link': 'disconnected'}):
            with self.subTest(**kwargs):
                a = app(**kwargs)
                with patch.object(desktop.messagebox, 'askyesno') as ask:
                    self.assertFalse(a.send_dm(PEER, '✉ EDC', Draft('hi')))
                ask.assert_not_called()
                a.control_request.assert_not_called()

    def test_confirmation_once_per_node_and_decline_sends_nothing(self):
        a = app()
        with patch.object(desktop.messagebox, 'askyesno', return_value=False):
            self.assertFalse(a.send_dm(PEER, '✉ EDC', Draft('hi')))
        a.control_request.assert_not_called()
        with patch.object(desktop.messagebox, 'askyesno', return_value=True) as ask:
            draft = Draft('you around?')
            self.assertTrue(a.send_dm(PEER, '✉ EDC', draft))
            self.assertTrue(a.send_dm(PEER, '✉ EDC', Draft('again')))
        self.assertEqual(ask.call_count, 1)
        self.assertEqual(draft.text, '')
        a.control_request.assert_any_call('dm', node=PEER, text='you around?')

    def test_end_conversation_requests_server(self):
        a = app()
        a.end_conversation(PEER)
        a.control_request.assert_called_once_with('dm_end', node=PEER)


class DesktopPollTests(unittest.TestCase):
    def poll_with(self, value):
        a = desktop.SitdownApp.__new__(desktop.SitdownApp)
        a.supervisor = Mock(poll_events=Mock(return_value=[('control', value)]), running=False, stopping=True)
        for name in ('status', 'radio_activity', 'link_warning', 'traffic', 'uptime', 'node_count'):
            setattr(a, name, Mock())
        a.snapshot = {'mqtt': {'status': 'Disabled'},
                      'nodes': [{'id': PEER, 'shortName': 'EDC', 'longName': 'Every Day Carry'}]}
        a.restart_at = None
        a.preflight_results = Mock(get_nowait=Mock(side_effect=desktop.queue.Empty))
        a.dual_mqtt, a.wire_views, a.chat_views = None, {}, {}
        a.chat_tabs, a.chat_tab_frames, a.dm_peers = None, {}, []
        a.chat_unread = desktop.Counter()
        a.nav_buttons = []
        a.chat_log = ChatLog()
        a.update_centralnj_status = Mock()
        a.auto_restart = Mock(get=Mock(return_value=False))
        a.demo = Mock(get=Mock(return_value=False))
        a.root = Mock(winfo_exists=Mock(return_value=False))
        a.show_info, a.log = Mock(), Mock()
        a.poll()
        return a

    def test_incoming_reply_is_stored_counted_and_logged_without_text(self):
        a = self.poll_with(dm_event(packet('secret plans'), interface()))
        self.assertEqual(a.chat_log.messages(f'dm:{PEER}')[0]['text'], 'secret plans')
        self.assertEqual(a.chat_unread[f'dm:{PEER}'], 1)
        line = a.log.call_args_list[0].args[0]
        self.assertIn('Direct message from EDC', line)
        self.assertNotIn('secret', line)

    def test_send_result_echo_and_error(self):
        echo = {'type': 'dm', 'peer': PEER, 'from': '!934cca70', 'short': '', 'long': '', 'text': 'hi',
                'rx_time': 1792626000, 'mine': True, 'id': 3, 'status': 'sending'}
        a = self.poll_with({'type': 'result', 'action': 'dm', 'text': 'submitted', 'chat': echo})
        self.assertEqual(a.chat_log.messages(f'dm:{PEER}')[0]['text'], 'hi')
        a.show_info.assert_not_called()
        bad = self.poll_with({'type': 'result', 'action': 'dm', 'error': 'Slow down'})
        bad.show_info.assert_called_once_with('Message not sent', 'Slow down')


if __name__ == '__main__':
    unittest.main()
