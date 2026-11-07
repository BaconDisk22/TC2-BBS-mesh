"""A radio that can't be reached gives one clear line and exit code 2, not a traceback."""
import errno
import unittest
from unittest.mock import Mock, patch

import server
import sitdown_desktop as desktop
from sitdown_monitor import LINK_ERROR_PATTERN
from sitdown_ui import state_of


class DescribeTests(unittest.TestCase):
    def test_network_radio_line_names_address_reason_and_fix(self):
        line = server.describe_connect_failure(OSError(errno.EHOSTUNREACH, 'No route to host'),
                                               {'interface_type': 'tcp', 'hostname': '10.0.0.76'})
        self.assertEqual(line, "Can't reach your radio at 10.0.0.76 (network): No route to host. "
                               'Radio network unreachable. Check the radio IP, Wi-Fi/network, and routing.')
        self.assertTrue(LINK_ERROR_PATTERN.search(line))        # the desktop still shows its hint

    def test_usb_and_value_errors(self):
        line = server.describe_connect_failure(ValueError('No serial ports detected.'),
                                               {'interface_type': 'serial', 'port': None})
        self.assertTrue(line.startswith("Can't reach your radio on the USB port (USB): No serial ports detected."))
        line = server.describe_connect_failure(OSError(errno.ECONNREFUSED, 'Connection refused'),
                                               {'interface_type': 'tcp', 'hostname': 'radio.local'})
        self.assertIn('Radio refused the TCP connection', line)


class MainTests(unittest.TestCase):
    def test_connection_failure_exits_2_without_raising(self):
        config = {'interface_type': 'tcp', 'hostname': '10.0.0.76', 'bbs_nodes': [], 'allowed_nodes': []}
        with patch.object(server, 'init_cli_parser', return_value=Mock(config=None)), \
             patch.object(server, 'initialize_config', return_value=config), \
             patch.object(server, 'merge_config'), \
             patch.object(server, 'display_banner'), \
             patch.object(server, 'get_interface', side_effect=OSError(errno.EHOSTUNREACH, 'No route to host')), \
             self.assertLogs(level='ERROR') as logs:
            self.assertEqual(server.main(), server.EXIT_RADIO_UNREACHABLE)
        self.assertIn("Can't reach your radio at 10.0.0.76", logs.output[0])
        self.assertNotIn('Traceback', logs.output[0])


class DesktopTests(unittest.TestCase):
    def test_exit_2_reads_as_cant_reach_radio_and_still_retries(self):
        a = desktop.SitdownApp.__new__(desktop.SitdownApp)
        process = Mock(poll=Mock(return_value=2))
        a.supervisor = Mock(poll_events=Mock(return_value=[]), running=False, stopping=False, process=process,
                            rx_count=0, tx_count=0)
        for name in ('status', 'radio_activity', 'link_warning', 'traffic', 'uptime', 'node_count'):
            setattr(a, name, Mock())
        a.snapshot = {'mqtt': {'status': 'Disabled'}}
        a.restart_at = None
        a.preflight_results = Mock(get_nowait=Mock(side_effect=desktop.queue.Empty))
        a.dual_mqtt, a.wire_views, a.chat_views = None, {}, {}
        a.update_centralnj_status = Mock()
        a.auto_restart = Mock(get=Mock(return_value=True))
        a.demo = Mock(get=Mock(return_value=False))
        a.root = Mock(winfo_exists=Mock(return_value=False))
        a.chat_log = Mock(render=Mock(return_value=''))
        a.retry_count = 0
        a.log, a.show_info = Mock(), Mock()
        with patch.object(desktop.time, 'monotonic', return_value=100):
            a.poll()
        a.status.set.assert_any_call("Can't reach radio")
        self.assertIn("couldn't reach the radio", a.log.call_args_list[0].args[0])
        self.assertEqual(a.retry_count, 1)                        # recovery still scheduled
        self.assertEqual(state_of("Can't reach radio"), 'error')


if __name__ == '__main__':
    unittest.main()
