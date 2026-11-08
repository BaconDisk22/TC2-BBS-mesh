"""The desktop's own node directory: beyond the radio's limit, from radio and MQTT, kept between restarts."""
import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import sitdown_desktop as desktop
from sitdown_nodedir import NodeDirectory, ago, format_entry, radio_row


class Clock:
    def __init__(self):
        self.now = 1_800_000_000

    def __call__(self):
        return self.now


def radio(number, heard, **extra):
    return {'id': f'!{number:08x}', 'shortName': f'N{number}', 'longName': f'Node {number}', 'hardware': 'RAK4631',
            'role': 'CLIENT', 'lastHeard': heard, 'snr': 5.5, 'hopsAway': 1,
            'deviceMetrics': {'batteryLevel': 80}, 'position': {'latitude': 40.2, 'longitude': -74.5}, **extra}


class DirectoryTests(unittest.TestCase):
    def test_keeps_nodes_the_radio_forgets(self):
        clock = Clock()
        nodes = NodeDirectory(clock=clock)
        nodes.record_radio([radio(n, clock.now - n) for n in range(1, 101)])        # a full radio list
        nodes.record_radio([radio(n, clock.now) for n in range(51, 151)])            # radio evicted 1–50
        self.assertEqual(nodes.count(), 150)
        total, rows = nodes.search(limit=1000)
        self.assertEqual(total, 150)
        self.assertEqual(rows[0]['last_heard'], clock.now)

    def test_radio_fields_and_newer_only_signal_updates(self):
        clock = Clock()
        nodes = NodeDirectory(clock=clock)
        nodes.record_radio([radio(7, clock.now - 600)])
        nodes.record_radio([radio(7, clock.now - 900, snr=-3.0, shortName='NEW')])   # older report
        row = nodes.search('!00000007')[1][0]
        self.assertEqual((row['short'], row['snr'], row['battery'], row['hops']), ('NEW', 5.5, 80, 1))
        self.assertAlmostEqual(row['latitude'], 40.2)
        self.assertEqual(row['radio_heard'], clock.now - 600)

    def test_mqtt_sightings_and_names(self):
        clock = Clock()
        nodes = NodeDirectory(clock=clock)
        self.assertTrue(nodes.record_mqtt('centralnj', 0x0000abcd))
        self.assertTrue(nodes.record_mqtt('sjmesh', 0x0000abcd, 'AB', 'Alpha Bravo'))
        self.assertFalse(nodes.record_mqtt('other', 0x0000abcd))
        self.assertFalse(nodes.record_mqtt('centralnj', 0xFFFFFFFF))
        self.assertEqual(nodes.record_mqtt_many([('centralnj', 1, '', ''), ('centralnj', 0, '', ''),
                                                 ('sjmesh', 2, 'X', '')]), 2)
        row = nodes.search('alpha')[1][0]
        self.assertEqual((row['short'], row['centralnj_heard'], row['sjmesh_heard']), ('AB', clock.now, clock.now))
        nodes.record_mqtt('centralnj', 0x0000abcd)                      # a later packet without names keeps them
        self.assertEqual(nodes.search('!0000abcd')[1][0]['long'], 'Alpha Bravo')

    def test_mqtt_details_fill_position_and_battery(self):
        nodes = NodeDirectory(clock=Clock())
        nodes.record_mqtt_many([('centralnj', 5, '', '', {'latitude': 40.1, 'longitude': -74.2, 'battery': 64}),
                                ('centralnj', 6, '', '', {'latitude': 95, 'longitude': 0, 'battery': 400})])
        five = nodes.search('!00000005')[1][0]
        self.assertEqual((five['latitude'], five['longitude'], five['battery']), (40.1, -74.2, 64))
        six = nodes.search('!00000006')[1][0]
        self.assertEqual((six['latitude'], six['battery']), (None, None))

    def test_filters_by_time_source_and_text(self):
        clock = Clock()
        nodes = NodeDirectory(clock=clock)
        nodes.record_radio([radio(1, clock.now - 10), radio(2, clock.now - 3 * 86400)])
        nodes.record_mqtt('sjmesh', 3, 'MQ', 'Only MQTT')
        self.assertEqual(nodes.search(since=clock.now - 86400)[0], 2)
        self.assertEqual(nodes.search(source='radio')[0], 2)
        self.assertEqual(nodes.search(source='mqtt')[0], 1)
        self.assertEqual(nodes.search('node 2')[0], 1)
        self.assertEqual(nodes.search('%')[0], 3)                       # wildcards are not special
        self.assertEqual(nodes.search('00000001')[0], 1)

    def test_bad_rows_are_skipped_and_values_checked(self):
        nodes = NodeDirectory(clock=Clock())
        nodes.record_radio([None, {'id': 'EDC'}, {'id': '!1'}, {'id': '!ffffffff'},
                            radio(9, 'soon', snr='x', deviceMetrics={'batteryLevel': 500},
                                  position={'latitude': 999, 'longitude': -74})])
        self.assertEqual(nodes.count(), 1)
        row = nodes.search()[1][0]
        self.assertEqual((row['snr'], row['battery'], row['latitude']), (None, None, None))
        self.assertIsNone(radio_row({'id': '!00000001', 'lastHeard': 1_900_000_000}, 1_800_000_000)['heard'])
        self.assertAlmostEqual(radio_row({'id': '!00000001', 'position': {'latitudeI': 402000000,
                                                                           'longitudeI': -745000000}},
                                         1)['latitude'], 40.2)

    def test_size_is_bounded_by_dropping_the_longest_unheard(self):
        clock = Clock()
        nodes = NodeDirectory(clock=clock, limit=10)
        for n in range(1, 16):
            clock.now += 1
            nodes.record_mqtt('centralnj', n)
        self.assertEqual(nodes.count(), 10)
        self.assertEqual(nodes.search('!00000001')[0], 0)
        self.assertEqual(nodes.search('!0000000f')[0], 1)

    def test_survives_restart_in_a_private_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'node-directory.db'
            nodes = NodeDirectory(path, clock=Clock())
            nodes.record_mqtt('centralnj', 5, 'FIVE', '')
            nodes.close()
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            again = NodeDirectory(path, clock=Clock())
            self.assertEqual(again.search('five')[0], 1)
            again.close()


class FormatTests(unittest.TestCase):
    def test_entry_lines(self):
        clock = Clock()
        nodes = NodeDirectory(clock=clock)
        nodes.record_radio([radio(1, clock.now - 7200)])
        nodes.record_mqtt('centralnj', 1)
        row = nodes.search()[1][0]
        text = format_entry(row, clock.now, radio_ids={'!00000001'})
        self.assertTrue(text.startswith('N1 (Node 1)  !00000001'))
        self.assertIn('last heard just now — in radio list · CentralNJ', text)
        self.assertIn('RAK4631 · 80% battery · SNR 5.5 · 1 hop · 40.2000, -74.5000', text)
        self.assertIn('heard by radio', format_entry(row, clock.now, radio_ids=set()))
        self.assertEqual([ago(s) for s in (30, 3600, 7200, 90000)],
                         ['just now', '1 hour ago', '2 hours ago', '1 day ago'])


class DesktopTests(unittest.TestCase):
    def app(self):
        a = desktop.SitdownApp.__new__(desktop.SitdownApp)
        a.node_directory = NodeDirectory(clock=Clock())
        a.node_count = Mock()
        return a

    def test_count_says_how_many_beyond_the_radio(self):
        a = self.app()
        a.node_directory.record_mqtt('sjmesh', 0x99)
        a.record_radio_nodes([radio(1, 1_799_999_000), radio(2, 1_799_999_000)])
        a.node_count.set.assert_called_with('Known nodes: 3 (radio holds 2)')

    def test_details_from_several_packets_are_merged_before_the_flush(self):
        a = self.app()
        summary = SimpleNamespace(from_id=0x77)
        a.record_mqtt_node('sjmesh', SimpleNamespace(accepted=True, summary=summary, short='', long='',
                                                     details={'battery': 50}))
        a.record_mqtt_node('sjmesh', SimpleNamespace(accepted=True, summary=summary, short='SV', long='',
                                                     details={'latitude': 39.9, 'longitude': -74.9}))
        a.flush_mqtt_nodes()
        row = a.node_directory.search()[1][0]
        self.assertEqual((row['short'], row['battery'], row['latitude']), ('SV', 50, 39.9))

    def test_mqtt_sightings_are_batched_and_names_kept(self):
        a = self.app()
        summary = SimpleNamespace(from_id=0x1234)
        for short in ('', 'TWO', ''):
            a.record_mqtt_node('centralnj', SimpleNamespace(accepted=True, summary=summary, short=short, long=''))
        a.record_mqtt_node('centralnj', SimpleNamespace(accepted=False, summary=SimpleNamespace(from_id=7)))
        self.assertEqual(a.node_directory.count(), 0)                    # nothing written until the flush
        a.flush_mqtt_nodes()
        rows = a.node_directory.search()[1]
        self.assertEqual([(r['id'], r['short']) for r in rows], [('!00001234', 'TWO')])


if __name__ == '__main__':
    unittest.main()
