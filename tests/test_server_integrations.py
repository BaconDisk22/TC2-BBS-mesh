"""Sitdown wiring for integrations: assistant posts use the operator chat path."""
import configparser
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import server
import sitdown_desktop


class AssistantSenderTests(unittest.TestCase):
    def test_posts_through_chat_send_and_tells_the_desktop(self):
        service = Mock()
        service.chat_send.return_value = {'type': 'chat', 'text': 'hi', 'mine': True}
        notes = []
        send = server.make_assistant_sender(service, notes.append)
        self.assertEqual(send('hi', 1, None)['text'], 'hi')
        service.chat_send.assert_called_once_with(1, 'hi', on_status=notes.append)
        self.assertEqual(notes[0]['action'], 'chat')
        self.assertIn('Assistant', notes[0]['text'])

    def test_direct_messages_refused(self):
        service = Mock()
        with self.assertRaises(ValueError):
            server.make_assistant_sender(service)('hi', 0, '!12345678')
        service.chat_send.assert_not_called()

    def test_rate_limit_error_reaches_the_assistant(self):
        service = Mock()
        service.chat_send.side_effect = ValueError('Slow down')
        with self.assertRaisesRegex(ValueError, 'Slow down'):
            server.make_assistant_sender(service)('hi', 0, None)


class StartIntegrationsTests(unittest.TestCase):
    def config(self, text=''):
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read_string(text)
        return {'config': cfg, 'interface_type': 'tcp'}

    def test_off_without_section_and_token_beside_config(self):
        folder = pathlib.Path(tempfile.mkdtemp())
        interface = SimpleNamespace(sitdown=Mock())
        hub = server.start_integrations(self.config(), str(folder / 'config.ini'), interface)
        self.assertFalse(hub.settings.any_enabled)
        self.assertIsNone(hub.server)
        self.assertEqual(hub.data_dir, folder.resolve() / 'integrations')
        self.assertTrue(hub.state.status()['radio']['connected'])

    def test_api_starts_when_enabled(self):
        import socket
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        folder = pathlib.Path(tempfile.mkdtemp())
        hub = server.start_integrations(self.config(f'[integrations]\nlocal_api_enabled = True\nlocal_api_port = {port}\n'),
                                        str(folder / 'config.ini'), SimpleNamespace(sitdown=Mock()))
        try:
            self.assertIsNotNone(hub.server)
            self.assertTrue((folder / 'integrations' / 'assistant-token').exists())
        finally:
            hub.stop()

    def test_receive_path_feeds_integrations_first(self):
        source = pathlib.Path(server.__file__).read_text(encoding='utf-8')
        body = source.split('def receive_packet(packet, interface):', 1)[1]
        self.assertTrue(body.lstrip().startswith('integrations.observe(packet)'))
        self.assertIn('integrations.stop()', source)

    def test_desktop_has_assistant_entry(self):
        source = pathlib.Path(sitdown_desktop.__file__).read_text(encoding='utf-8')
        self.assertIn('--sitdown-assistant-mcp', source)


if __name__ == '__main__':
    unittest.main()
