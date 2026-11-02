"""Desktop regressions; real-window checks run on the Intel macOS build runner."""
import os
import queue
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

import sitdown_desktop as desktop


class DesktopTests(unittest.TestCase):
    def test_send_safety_stays_blocked_and_does_not_access_credentials_or_transport(self):
        app = desktop.SitdownApp.__new__(desktop.SitdownApp)
        app.centralnj_send_enabled = Mock()
        app.demo = Mock()
        app.show_info = Mock()
        app.read_config = Mock(side_effect=AssertionError("read credentials"))
        app.control_request = Mock(side_effect=AssertionError("requested transmission"))
        app.supervisor = Mock()
        with patch("socket.socket", side_effect=AssertionError("opened network socket")), \
             patch.object(desktop, "ServerSupervisor", side_effect=AssertionError("started radio")):
            for enabled, demo, reason in (
                (False, False, "Transmission opt-in disabled"),
                (True, False, "Operator confirmation required"),
                (False, True, "Demo mode: transmissions disabled"),
                (True, True, "Demo mode: transmissions disabled"),
            ):
                with self.subTest(enabled=enabled, demo=demo):
                    app.centralnj_send_enabled.get.return_value = enabled
                    app.demo.get.return_value = demo
                    self.assertFalse(app.check_centralnj_send_safety())
                    app.show_info.assert_called_with("CentralNJ Send Safety", reason)
        app.read_config.assert_not_called()
        app.control_request.assert_not_called()
        self.assertEqual(app.supervisor.mock_calls, [])

    def test_preflight_snapshots_inputs_and_guards_repeated_checks_and_start(self):
        app = desktop.SitdownApp.__new__(desktop.SitdownApp)
        app.preflight_pending = False
        app.preflight_results = queue.Queue()
        app.connection = Mock(get=Mock(return_value='tcp'))
        app.address = Mock(get=Mock(return_value='radio.local'))
        app.demo = Mock(get=Mock(return_value=False))
        app.supervisor = None
        app.restart_at = 123
        app.log = Mock()
        app.show_info = Mock()
        app.save = Mock()
        with patch.object(desktop.threading, 'Thread') as thread, \
             patch.object(desktop, 'check_radio', return_value='report') as check:
            app.check_radio_connection()
            thread.assert_called_once()
            self.assertTrue(thread.call_args.kwargs['daemon'])
            app.address.get.return_value = 'other.local'
            app.check_radio_connection()
            app.start()
            app.save.assert_not_called()
            thread.call_args.kwargs['target']()
            check.assert_called_once_with('tcp', 'radio.local', demo=False, busy=True)
            self.assertEqual(app.preflight_results.get_nowait(), 'report')

    def test_preflight_worker_failure_is_reported_without_tk_calls(self):
        app = desktop.SitdownApp.__new__(desktop.SitdownApp)
        app.preflight_pending = False
        app.preflight_results = queue.Queue()
        app.connection = Mock(get=Mock(return_value='serial'))
        app.address = Mock(get=Mock(return_value='/dev/cu.radio'))
        app.demo = Mock(get=Mock(return_value=False))
        app.supervisor = None
        app.restart_at = None
        app.log = Mock()
        app.show_info = Mock()
        with patch.object(desktop.threading, 'Thread') as thread, \
             patch.object(desktop, 'check_radio', side_effect=RuntimeError('failure')):
            app.check_radio_connection()
            thread.call_args.kwargs['target']()
            self.assertIn('Unverified', app.preflight_results.get_nowait())
            app.show_info.assert_not_called()

    def test_connection_summary_has_real_line_break_for_both_transports(self):
        app = desktop.SitdownApp.__new__(desktop.SitdownApp)
        app.connection_summary = Mock()
        app.address = Mock()
        app.address.get.return_value = " 10.0.0.76 "
        app.connection = Mock()
        for transport, name in (("tcp", "TCP / Network"), ("serial", "USB / Serial")):
            app.connection.get.return_value = transport
            app.refresh_connection_summary()
            self.assertEqual(app.connection_summary.set.call_args.args[0].splitlines(),
                             [f"Configured: {name}", "Endpoint: 10.0.0.76"])

    def test_recovery_backoff_is_opt_in_and_capped(self):
        app = desktop.SitdownApp.__new__(desktop.SitdownApp)
        app.auto_restart = Mock(get=Mock(return_value=True))
        app.demo = Mock(get=Mock(return_value=False))
        app.log = Mock()
        app.status = Mock()
        app.retry_count = 0
        app.restart_at = None
        with patch.object(desktop.time, "monotonic", return_value=100):
            for attempt in range(1, 9):
                app.schedule_recovery()
                self.assertEqual(app.retry_count, attempt)
                self.assertEqual(app.restart_at, 100 + min(60, 2 ** min(attempt, 6)))
        app.auto_restart.get.return_value = False
        app.restart_at = None
        app.schedule_recovery()
        self.assertIsNone(app.restart_at)
        app.auto_restart.get.return_value = True
        app.demo.get.return_value = True
        app.schedule_recovery()
        self.assertIsNone(app.restart_at)

    def test_explicit_stop_cancels_pending_recovery(self):
        app = desktop.SitdownApp.__new__(desktop.SitdownApp)
        app.restart_at = 200
        app.retry_count = 4
        app.supervisor = None
        app.status = Mock()
        app.radio_activity = Mock()
        app.uptime = Mock()
        app.link_warning = Mock()
        app.log = Mock()
        app.stop()
        self.assertIsNone(app.restart_at)
        self.assertEqual(app.retry_count, 0)

    def test_start_passes_transport_and_endpoint_and_stop_cancels_restart(self):
        for transport, flag, address in (("tcp", "--host", "10.0.0.76"),
                                         ("serial", "--port", "/dev/cu.usbmodem1")):
            with self.subTest(transport=transport), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                (root / "config.ini").touch()
                app = desktop.SitdownApp.__new__(desktop.SitdownApp)
                app.supervisor = None
                app.demo = Mock(get=Mock(return_value=False))
                app.connection = Mock(get=Mock(return_value=transport))
                app.address = Mock(get=Mock(return_value=address))
                app.status = Mock()
                app.uptime = Mock()
                app.link_warning = Mock()
                app.save = Mock()
                app.log = Mock()
                with patch.object(desktop, "ROOT", root), patch.object(desktop, "DATA", root), \
                     patch.object(desktop, "ServerSupervisor") as factory:
                    app.start()
                    args = factory.call_args.args[0]
                    self.assertEqual(args[-4:], ["--interface-type", transport, flag, address])
                    monitor = factory.return_value
                    monitor.start.assert_called_once()
                    app.restart_at = 100
                    app.retry_count = 2
                    app.stop()
                    monitor.stop.assert_called_once()
                    self.assertIsNone(app.restart_at)
                    self.assertIsNone(app.supervisor)
                    app.status.set.assert_called_with("Stopped")


@unittest.skipUnless(os.environ.get("SITDOWN_UI_TESTS") == "1", "requires a GUI session")
class DesktopWindowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.config_patch = patch.object(desktop, "CONFIG", Path(self.directory.name) / "launcher.json")
        self.config_patch.start()
        self.root = tk.Tk()
        self.app = desktop.SitdownApp(self.root)
        self.root.update()

    def tearDown(self):
        self.root.destroy()
        self.config_patch.stop()
        self.directory.cleanup()

    def test_send_preflight_opt_in_defaults_off_and_is_not_restored(self):
        self.assertFalse(self.app.centralnj_send_enabled.get())
        self.app.centralnj_send_enabled.set(True)
        with patch.object(desktop, "DATA", Path(self.directory.name)):
            self.app.save()
        self.assertNotIn("centralnj_send_enabled", desktop.CONFIG.read_text())
        self.root.destroy()
        self.root = tk.Tk()
        self.app = desktop.SitdownApp(self.root)
        self.root.update()
        self.assertFalse(self.app.centralnj_send_enabled.get())
        with patch.object(self.app, "show_info") as show:
            self.assertFalse(self.app.check_centralnj_send_safety())
            show.assert_called_once_with("CentralNJ Send Safety", "Transmission opt-in disabled")

    def test_all_navigation_controls_fit_after_resize_and_larger_fonts(self):
        for size in (12, 18):
            for control in self.app.nav_buttons:
                control.configure(font=("Helvetica", size, "bold"))
            for width, height in ((1120, 740), (1280, 900), (1600, 1000), (1120, 740)):
                self.root.geometry(f"{width}x{height}")
                self.root.update()
                nav = self.app.navigation
                for control in self.app.nav_buttons:
                    with self.subTest(size=size, width=width, title=control.cget("text")):
                        self.assertTrue(control.winfo_ismapped())
                        self.assertGreaterEqual(control.winfo_x(), 0)
                        self.assertLessEqual(control.winfo_x() + control.winfo_width(), nav.winfo_width())
                        self.assertLessEqual(control.winfo_y() + control.winfo_height(), nav.winfo_height())
        for title in ("SETTINGS", "ABOUT"):
            control = next(c for c in self.app.nav_buttons if c.cget("text").split()[-1] == title)
            control.event_generate("<Button-1>")
            self.root.update()
            self.assertIsNotNone(self.app.active_page)
            self.assertTrue(self.app.active_page.winfo_ismapped())

    def test_preflight_button_reports_on_ui_thread_without_mutating_settings(self):
        self.app.demo.set(True)
        self.app.address.set('radio.local')
        self.app.connection.set('tcp')
        with patch.object(desktop.threading, 'Thread') as thread, \
             patch.object(desktop, 'check_radio', return_value='Protocol unverified') as check, \
             patch.object(self.app, 'show_info') as show:
            def descendants(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from descendants(child)
            button = next(w for w in descendants(self.app.home_page)
                          if isinstance(w, tk.Label) and w.cget('text') == 'Check Radio Connection')
            button.event_generate('<Button-1>')
            self.root.update()
            thread.call_args.kwargs['target']()
            self.app.poll()
            check.assert_called_once_with('tcp', 'radio.local', demo=True, busy=False)
            show.assert_called_once_with('Radio Connection Preflight', 'Protocol unverified')
            self.assertFalse(self.app.preflight_pending)
            self.assertEqual(self.app.address.get(), 'radio.local')
            self.assertFalse(desktop.CONFIG.exists())

    def test_each_page_opens_in_demo_and_returns_home(self):
        self.app.demo.set(True)
        self.app.start()
        for title in ('BBS', 'MESSAGES', 'BOARDS', 'RADIO', 'NODES', 'SETTINGS', 'ABOUT'):
            self.app.select_page(title)
            self.root.update()
            self.assertTrue(self.app.active_page.winfo_ismapped())
        self.app.select_page('HOME')
        self.root.update()
        self.assertTrue(self.app.home_page.winfo_ismapped())
        with patch.object(self.app, 'show_info'):
            self.app.supervisor = Mock()
            self.app.control_request('announce', channel=0, text='Demo')
            self.app.supervisor.request.assert_not_called()

    def test_wire_shows_each_network_separately_and_updates_live(self):
        import configparser
        from meshtastic.protobuf import mqtt_pb2
        from sitdown_dual_mqtt import DualMQTTMonitors
        env = mqtt_pb2.ServiceEnvelope()
        env.gateway_id, env.channel_id = '!12345678', 'LongFast'
        env.packet.id = 7
        setattr(env.packet, 'from', 0x7b)
        env.packet.to = 0xFFFFFFFF
        env.packet.encrypted = b'ciphertext'
        self.app.dual_mqtt = DualMQTTMonitors(configparser.ConfigParser())
        self.app.select_page('BBS')
        self.root.update()
        self.app.dual_mqtt.receive('centralnj', env.SerializeToString())
        self.app.dual_mqtt.receive('centralnj', b'junk')
        self.app.poll()
        central = self.app.wire_views['centralnj'].get('1.0', 'end')
        south = self.app.wire_views['sjmesh'].get('1.0', 'end')
        self.assertIn('from !0000007b', central)
        self.assertIn('ENCRYPTED', central)
        self.assertIn('Accepted 1 · duplicates 0 · rejected 1 (invalid mqtt envelope 1)', central)
        self.assertNotIn('!0000007b', south)
        self.app.dual_mqtt = None

    def test_server_log_stays_readable_at_every_window_size(self):
        linespace = int(self.app.output.tk.call('font', 'metrics', self.app.output.cget('font'), '-linespace'))
        for geometry in ('1160x760', '1160x940', '1160x1040', '1600x1000'):
            with self.subTest(geometry=geometry):
                self.root.geometry(geometry)
                self.root.update()
                self.assertGreaterEqual(self.app.output.winfo_height() // linespace, desktop.LOG_MIN_LINES)

    def test_every_settings_field_is_visible_or_scrollable(self):
        from tkinter import ttk
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        for geometry in ('1160x760', '1160x940'):
            self.root.geometry(geometry)
            self.app.select_page('SETTINGS')
            self.root.update()
            notebook = next(w for w in descendants(self.app.active_page) if isinstance(w, ttk.Notebook))
            for index, tab in enumerate(notebook.tabs()):
                notebook.select(index)
                self.root.update()
                self.root.update()
                holder = self.root.nametowidget(tab)
                canvas = next(w for w in holder.winfo_children() if isinstance(w, tk.Canvas))
                bar = next(w for w in holder.winfo_children() if isinstance(w, ttk.Scrollbar))
                fits = canvas.winfo_children()[0].winfo_reqheight() <= canvas.winfo_height()
                with self.subTest(geometry=geometry, tab=notebook.tab(index, 'text')):
                    self.assertTrue(fits or bar.winfo_ismapped())
                    if geometry == '1160x940':
                        self.assertTrue(fits)
            save = next(w for w in descendants(self.app.active_page)
                        if isinstance(w, tk.Label) and w.cget('text').startswith('SAVE'))
            self.assertTrue(save.winfo_ismapped())
            self.assertLessEqual(save.winfo_rooty() + save.winfo_height(),
                                 self.root.winfo_rooty() + self.root.winfo_height())

    def test_chat_page_shows_messages_in_their_channel_tab_live(self):
        from sitdown_chat import BROADCAST, chat_event
        from types import SimpleNamespace
        iface = SimpleNamespace(myInfo=SimpleNamespace(my_node_num=1), nodes={})
        def heard(text, channel):
            return chat_event({'from': 0x02ed8680, 'fromId': '!02ed8680', 'to': BROADCAST, 'channel': channel,
                               'rxTime': 1792616310, 'decoded': {'portnum': 'TEXT_MESSAGE_APP',
                                                                 'payload': text.encode()}}, iface)
        self.app.snapshot = {**self.app.snapshot, 'channels': [{'index': 0, 'name': 'LongFast'},
                                                               {'index': 1, 'name': 'CentralNJ'}]}
        self.app.select_page('CHAT')
        self.root.update()
        self.assertEqual(sorted(self.app.chat_views), [0, 1])
        self.app.chat_log.add(heard('Ayyy from CentralNJ', 1))
        self.app.refresh_chat_views()
        central = self.app.chat_views[1][1].get('1.0', 'end')
        longfast = self.app.chat_views[0][1].get('1.0', 'end')
        self.assertIn('Ayyy from CentralNJ', central)
        self.assertNotIn('Ayyy from CentralNJ', longfast)

    def test_chat_tabs_have_compose_box_and_byte_counter(self):
        from tkinter import ttk
        self.app.snapshot = {**self.app.snapshot, 'channels': [{'index': 0, 'name': 'Primary'},
                                                               {'index': 1, 'name': 'CentralNJ'}]}
        self.app.select_page('CHAT')
        self.root.update()
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        widgets = list(descendants(self.app.active_page))
        self.assertEqual(len([w for w in widgets if isinstance(w, ttk.Entry)]), 2)
        self.assertEqual(len([w for w in widgets if isinstance(w, tk.Label) and w.cget('text') == 'SEND']), 2)
        entry = next(w for w in widgets if isinstance(w, ttk.Entry))
        entry.insert(0, 'Ayyy é')
        self.root.update()
        self.assertTrue(any(isinstance(w, tk.Label) and w.cget('textvariable') and
                            self.root.globalgetvar(w.cget('textvariable')) == '7/200 bytes' for w in widgets))

    def test_unread_counts_on_chat_button_and_tabs(self):
        from sitdown_chat import BROADCAST, chat_event
        from types import SimpleNamespace
        iface = SimpleNamespace(myInfo=SimpleNamespace(my_node_num=1), nodes={})
        def heard(text, channel, sender=0x02ed8680):
            return chat_event({'from': sender, 'fromId': f'!{sender:08x}', 'to': BROADCAST, 'channel': channel,
                               'rxTime': 1792616310, 'decoded': {'portnum': 'TEXT_MESSAGE_APP',
                                                                 'payload': text.encode()}}, iface)
        chat = next(b for b in self.app.nav_buttons if b.page_name == 'CHAT')
        self.app.snapshot = {**self.app.snapshot, 'channels': [{'index': 0, 'name': 'Primary'},
                                                               {'index': 1, 'name': 'CentralNJ'}]}
        for text in ('one', 'two'):                       # CHAT closed: everything is unread
            self.app.chat_log.add(heard(text, 1))
            self.app.note_chat_message(heard(text, 1))
        self.assertTrue(chat.cget('text').endswith('CHAT (2)'))
        self.app.select_page('CHAT')                      # opens on Primary; CentralNJ still unread
        self.root.update()
        tabs = self.app.chat_tabs
        self.assertEqual(tabs.tab(self.app.chat_tab_frames[1], 'text'), 'CentralNJ (2)')
        self.assertEqual(chat.cget('bg'), self.app.colors['primary'])   # still highlighted with a count
        self.app.note_chat_message(heard('on screen', 0))                # visible tab: not counted
        self.app.note_chat_message({**heard('mine', 1), 'mine': True})   # own message: not counted
        self.assertEqual(self.app.chat_unread[1], 2)
        tabs.select(self.app.chat_tab_frames[1])
        self.root.update()
        self.assertEqual(tabs.tab(self.app.chat_tab_frames[1], 'text'), 'CentralNJ')
        self.assertEqual(chat.cget('text'), chat.base_text)
        self.app.select_page('HOME')
        self.root.update()
        self.app.note_chat_message(heard('later', 1))
        self.assertTrue(chat.cget('text').endswith('CHAT (1)'))

    def test_settings_save_preserves_existing_config_and_validates(self):
        path = Path(self.directory.name) / 'config.ini'
        path.write_text('[interface]\ntype = tcp\nhostname = 192.0.2.10\n[menu]\nmain_menu_items = B,X\n')
        with patch.object(self.app, 'config_path', return_value=path), patch.object(self.app, 'show_info'):
            self.app.select_page('SETTINGS')
            self.root.update()
            fields = self.app.setting_fields
            fields[('startup', 'enabled')].set(True)
            fields[('startup', 'message')].set('Hello Jersey')
            def descendants(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from descendants(child)
            button = next(w for w in descendants(self.app.active_page)
                          if isinstance(w, tk.Label) and w.cget('text').startswith('SAVE'))
            button.event_generate('<Button-1>')
            config = self.app.read_config()
            self.assertEqual(config.get('interface', 'hostname'), '192.0.2.10')
            self.assertEqual(config.get('menu', 'main_menu_items'), 'B,X')
            self.assertTrue(config.getboolean('startup', 'enabled'))
            self.assertEqual(config.get('startup', 'message'), 'Hello Jersey')
            original = path.read_text()
            fields[('startup', 'channel')].set('99')
            button.event_generate('<Button-1>')
            self.assertEqual(path.read_text(), original)

    def test_info_is_themed_nonblocking_and_keyboard_dismissible(self):
        for key in ("<Escape>", "<Return>", "<space>"):
            win = self.app.show_info("Radio Connection", "First line\nSecond line")
            self.root.update()
            self.assertEqual(win.cget("bg"), desktop.THEME["background"])
            self.assertEqual(str(win.transient()), str(self.root))
            self.assertIsNone(self.root.grab_current())
            ran = []
            self.root.after(0, lambda: ran.append(True))
            self.root.update()
            self.assertTrue(ran)
            control = next(w for w in win.winfo_children() if isinstance(w, tk.Label) and w.cget("text") == "OK")
            control.focus_force()
            self.root.update()
            control.event_generate(key)
            self.root.update()
            self.assertFalse(win.winfo_exists())
