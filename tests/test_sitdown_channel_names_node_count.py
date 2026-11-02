"""Blank primary channel shows as Primary; HOME counts nodes from the radio snapshot."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import sitdown_desktop as desktop
from sitdown_utilities import LocalUtilities


def channel(name, role=1):
    return SimpleNamespace(role=role, settings=SimpleNamespace(name=name))


class ChannelNameTests(unittest.TestCase):
    def test_blank_primary_is_named_primary_and_disabled_slots_skipped(self):
        service = LocalUtilities.__new__(LocalUtilities)
        service.interface = SimpleNamespace(localNode=SimpleNamespace(channels=[
            channel(''), channel('ConnNet', 2), channel('', 2), channel('Unused', 0), channel('CentralNJ', 2)]))
        self.assertEqual(service.channels(), [
            {'index': 0, 'name': 'Primary'}, {'index': 1, 'name': 'ConnNet'},
            {'index': 2, 'name': 'Channel 2'}, {'index': 4, 'name': 'CentralNJ'}])


class NodeCountTests(unittest.TestCase):
    def test_snapshot_sets_known_node_count(self):
        app = desktop.SitdownApp.__new__(desktop.SitdownApp)
        snapshot = {'type': 'snapshot', 'nodes': [{'id': '!1'}, {'id': '!2'}, {'id': '!3'}],
                    'channels': [], 'mqtt': {'status': 'Disabled'}}
        app.supervisor = Mock(poll_events=Mock(return_value=[('control', snapshot)]), running=False, stopping=True)
        for name in ('status', 'radio_activity', 'link_warning', 'traffic', 'uptime', 'node_count'):
            setattr(app, name, Mock())
        app.snapshot = {'mqtt': {'status': 'Disabled'}}
        app.restart_at = None
        app.preflight_results = Mock(get_nowait=Mock(side_effect=desktop.queue.Empty))
        app.dual_mqtt = None
        app.wire_views, app.chat_views = {}, {}
        app.chat_log = desktop.ChatLog()
        app.update_centralnj_status = Mock()
        app.auto_restart = Mock(get=Mock(return_value=False))
        app.demo = Mock(get=Mock(return_value=False))
        app.root = Mock(winfo_exists=Mock(return_value=False))
        app.poll()
        app.node_count.set.assert_called_with('Known nodes: 3')


if __name__ == '__main__':
    unittest.main()
