#!/usr/bin/env python3
"""The Sitdown BBS desktop launcher. Standard-library Tkinter GUI."""
import json
import queue
from collections import Counter
import threading
from sitdown_radio_preflight import check_radio
from sitdown_send_preflight import send_preflight
from sitdown_status_view import format_operator_status
import configparser
import sqlite3
from tkinter import ttk
from sitdown_online import WeatherDesk, ActivityCounter
import runpy
import shutil
from pathlib import Path
import subprocess
import sys
import time
import tkinter as tk
from tkinter import messagebox, filedialog
from sitdown_backup import create_backup_archive
from sitdown_restore_preflight import inspect_backup_archive
from sitdown_restore import RESTORABLE, restore_backup_archive, restore_plan
from sitdown_errors import explain_transport_error
from sitdown_monitor import ServerSupervisor
from sitdown_mqtt_feed import MeshFeed, NETWORK_LABELS, format_stats
from sitdown_chat import ChatLog, dm_peer, event_key, mqtt_room, status_label
from sitdown_dm import peer_id
from sitdown_mqtt_crypto import keys_for_topics, unmatched_key_names
from sitdown_mqtt_sender import MQTTSender, send_rooms
from sitdown_nodedir import NodeDirectory, format_entry
from sitdown_version import VERSION
from sitdown_personality import Personality
from sitdown_ui import PALETTES, SPACE, UI, set_text
import zipfile

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
DATA = Path.home() / ".sitdown-bbs"
CONFIG = DATA / "launcher.json"
THEME = {}


def apply_theme_tokens(mode):
    """Fill THEME with the palette for mode, plus the legacy key names older code still reads."""
    palette = PALETTES.get(mode, PALETTES['dark'])
    THEME.clear()
    THEME.update(palette)
    THEME.update(background=palette['bg'], panel=palette['surface'], primary=palette['accent'],
                 connected=palette['success'], highlight=palette['muted'])


apply_theme_tokens('dark')
LOG_MIN_LINES = 8
COMMANDS = [
    ("/sitdown", "Main menu"), ("/help", "Quick help"),
    ("/mail", "Check your mail"), ("/boards", "The Social Club"),
    ("/fortune", "Jersey wisdom"), ("/tools", "Utilities"),
    ("/cancel", "Return to main menu"),
    ("/node <ID or name>", "Who’s Who"), ("/weather", "Jersey Weather Desk"),
    ("/wire", "MQTT activity"), ("/announce <channel> <text>", "Admin broadcast"),
]
# Sidebar groups: (group, ((page key, icon, label), ...)). Page keys are stable identifiers.
NAV = (
    ('Overview', (('HOME', '⌂', 'Dashboard'), ('RADIO', '◉', 'Radio'))),
    ('Mesh', (('CHAT', '✆', 'Chat'), ('NODES', '◎', 'Nodes'), ('WIRE', '≋', 'Network'))),
    ('BBS', (('MESSAGES', '✉', 'Mail'), ('BOARDS', '▤', 'Boards'), ('BBS', '⚑', 'Broadcast'))),
    ('App', (('SETTINGS', '⚙', 'Settings'), ('ABOUT', 'ⓘ', 'About'))),
)
PAGE_INFO = {
    'HOME': ('Dashboard', 'Server, radio and networks at a glance'),
    'RADIO': ('Radio', 'Connection details and channels from your radio'),
    'CHAT': ('Chat', 'Channel chat, direct messages and MQTT channels'),
    'NODES': ('Nodes', 'Everyone your radio has heard'),
    'WIRE': ('Network', 'MQTT broker links and packet activity'),
    'MESSAGES': ('Mail', 'Private mail stored by your BBS'),
    'BOARDS': ('Boards', 'Public bulletins on The Social Club'),
    'BBS': ('Broadcast', 'Announcements, weather and radio commands'),
    'SETTINGS': ('Settings', 'Server, networks and app preferences'),
    'ABOUT': ('About', 'Version, credits and help'),
}
NETWORK_HELP = {
    'topic': ('Topics to watch', 'Up to 4, comma-separated. Use msh/<region>/2/e/<channel>/+ to follow one channel '
              'from every gateway, e.g. msh/US/2/e/CentralNJ/+.'),
    'channel_keys': ('Channel keys', 'Lets the app read (and, if allowed, send) text on a channel. Format '
                     'Name=key, comma-separated, e.g. LongFast=AQ==. Stored only on this Mac.'),
}


def network_fields(label, host):
    return [
        ('#', 'Connection', '', None),
        ('enabled', f'Use {label}', 'bool', 'Turn this network on. Nothing connects until you also press Connect '
         'or turn on auto-connect.'),
        ('auto_connect', 'Connect when the app opens', 'bool', None),
        ('host', 'Broker address', host, None),
        ('port', 'Port', '8883', '8883 for TLS, 1883 without.'),
        ('tls', 'Encrypt the connection (TLS)', 'bool_true', 'Leave on unless the broker only offers port 1883.'),
        ('username', 'Username', '', None),
        ('password', 'Password', '', 'Stored only in your local config file.'),
        ('#', 'Channels', '', None),
        ('topic', *NETWORK_HELP['topic'][:1], '', NETWORK_HELP['topic'][1]),
        ('channel_keys', *NETWORK_HELP['channel_keys'][:1], '', NETWORK_HELP['channel_keys'][1]),
        ('#', 'Sending', '', 'Off by default. When on, each keyed channel tab in Chat gets a Send box.'),
        ('allow_publish', f'Allow sending to {label} from Chat', 'bool', 'You still confirm once per channel each session.'),
        ('send_as', 'Send as node id', '', 'Leave blank to use your BBS radio\'s id.'),
    ]


# section: (tab title, [(key, label, default, help)]). key '#' starts a titled group.
SETTINGS_SECTIONS = {
    'startup': ('Startup', [
        ('#', 'Startup announcement', '', 'Optionally say hello on a channel each time the server starts.'),
        ('enabled', 'Announce when the server starts', 'bool', 'At most once per start, with a cooldown.'),
        ('channel', 'Channel number', '0', '0–7, as numbered on your radio.'),
        ('message', 'Message', '', 'Leave blank to use the greeting style from Personality. Up to 200 bytes.'),
    ]),
    'announcements': ('Admins', [
        ('#', 'Remote administrators', '', 'Nodes allowed to send /announce over the radio.'),
        ('admin_nodes', 'Admin node ids', '', 'Comma-separated, like !934cca70, !b2c649cb.'),
    ]),
    'weather': ('Weather', [
        ('#', 'Weather desk', '', 'What /weather replies with.'),
        ('internet_enabled', 'Use internet weather', 'bool', 'Fetches from Open-Meteo for the location below.'),
        ('local_report', 'Offline report', '', 'Used when internet weather is off or unavailable. Include the time.'),
        ('latitude', 'Latitude', '40.20', None),
        ('longitude', 'Longitude', '-74.50', None),
    ]),
    'mqtt_centralnj': ('CentralNJ', network_fields('CentralNJ', 'mqtt.meshtastic.org')),
    'mqtt_sjmesh': ('SJMesh', network_fields('SJMesh', 'mqtt.sjmesh.net')),
    'personality': ('Personality', [
        ('#', 'Public greeting', '', 'How the BBS talks to people over the radio.'),
        ('public_mode', 'Public greeting style', 'clean', 'clean, attitude or uncensored.'),
        ('allow_public_profanity', 'Allow profanity in public greetings', 'bool', None),
    ]),
    'mqtt_monitor': ('Advanced', [
        ('#', 'BBS server MQTT counter', '', 'An older, server-side counter shown under Network. Most people can '
         'leave it off and use the CentralNJ / SJMesh tabs instead.'),
        ('enabled', 'Turn on the server counter', 'bool', None),
        ('host', 'Broker address', '', None),
        ('topic', 'Topic', '', 'One narrow topic. Broad topics like msh/US/# are refused.'),
        ('tls', 'Encrypt the connection (TLS)', 'bool_true', None),
        ('port', 'Port', '8883', None),
        ('username', 'Username', '', None),
        ('password', 'Password', '', None),
    ]),
}
SETUP_GUIDE = (
    "Setting up CentralNJ two-way through your radio:\n\n"
    "1. Add the CentralNJ channel and its key on your Meshtastic radio.\n"
    "2. Set the radio's MQTT broker to mqtt.meshtastic.org and enter the broker login on the radio.\n"
    "3. Turn on MQTT uplink and downlink for the CentralNJ channel, and set the regional root topic.\n"
    "4. Connect the radio to this app by USB or network and test with a short message.\n\n"
    "The CentralNJ and SJMesh tabs in Settings are separate: they let this app watch (and, if you allow it, "
    "send to) the broker directly. Keep topics narrow; avoid msh/US/# on limited hardware."
)

class SitdownApp:
    def __init__(self, root):
        self.root = root
        self.snapshot = {'nodes': [], 'channels': [], 'mqtt': {'status': 'Disabled', 'total': 0, 'topics': []}}
        self.active_page = None
        self.preflight_results = queue.Queue()
        self.preflight_pending = False
        self.proc = None
        self.dual_mqtt = None
        self.mesh_feed = MeshFeed()
        self.wire_views = {}
        # Beside launcher.json so tests that redirect CONFIG never touch real history.
        self.chat_log = ChatLog(path=CONFIG.with_name('chat-history.json'))
        self.node_directory = self.open_node_directory()
        self.chat_views = {}
        self.chat_confirmed = set()
        self.chat_unread = Counter()
        self.chat_tabs = None
        self.chat_tab_frames = {}
        self.dm_peers = []
        self.dm_confirmed = set()
        self.mqtt_sender = MQTTSender()
        self.mqtt_confirmed = set()
        self.mqtt_pending = {}
        self.supervisor = None
        self.restart_at = None
        self.retry_count = 0
        self.auto_restart = tk.BooleanVar(value=False)
        saved = self.directory().count() if getattr(self, 'node_directory', None) is not None else 0
        self.node_count = tk.StringVar(value=f'Known nodes: {saved} saved')
        self.uptime = tk.StringVar(value='Uptime: 0s')
        self.radio_activity = tk.StringVar(value='Radio activity: Not observed')
        self.traffic = tk.StringVar(value='Messages observed: RX 0 / TX 0')
        self.link_warning = tk.StringVar(value='Radio: not connected')
        self.centralnj_status = tk.StringVar(value=format_operator_status())
        self.centralnj_send_enabled = tk.BooleanVar(value=False)
        self.auto_start = tk.BooleanVar(value=False)
        self.demo = tk.BooleanVar(value=False)
        self.connection = tk.StringVar(value="serial")
        self.address = tk.StringVar()
        self.community = tk.StringVar(value="The Sitdown BBS")
        self.welcome = tk.StringVar(value="Ayyy, pull up a chair. We gotta talk.")
        self.desktop_mouth = tk.StringVar(value="clean")
        self.public_mouth = tk.StringVar(value="clean")
        self.public_profanity = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Stopped")
        self.mqtt_states = {name: tk.StringVar(value='Off') for name in NETWORK_LABELS}
        self.mqtt_summary = tk.StringVar(value='MQTT: off')
        self.appearance = tk.StringVar(value='dark')
        self.text_size = tk.StringVar(value='default')
        self.onboarded = False
        self.current_page = 'HOME'
        self.colors = THEME
        self.root.title("The Sitdown BBS")
        height = max(760, min(960, self.root.winfo_screenheight() - 80))
        self.root.geometry(f"1180x{height}")
        self.root.minsize(960, 700)
        self.load()
        apply_theme_tokens(self.appearance.get())
        self.ui = UI(self.root, self.appearance.get(), self.text_size.get())
        self.root.configure(bg=THEME['bg'])
        self.build()
        self.bind_shortcuts()
        self.appearance.trace_add('write', lambda *_: self.apply_appearance())
        self.text_size.trace_add('write', lambda *_: self.apply_text_size())
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(250, self.poll)
        self.root.after(600, self.start_opted_in_mqtt)
        if self.auto_start.get():
            self.root.after(200, self.start)

    def load(self):
        if CONFIG.exists():
            try:
                data = json.loads(CONFIG.read_text(encoding="utf-8"))
                self.auto_start.set(data.get("auto_start", False) is True)
                self.connection.set(data.get("connection", "serial"))
                self.address.set(data.get("address", ""))
                self.community.set(data.get("community", "The Sitdown BBS"))
                self.welcome.set(data.get("welcome", self.welcome.get()))
                desktop_mode = data.get("desktop_mouth", "clean")
                public_mode = data.get("public_mouth", "clean")
                enabled = data.get("public_profanity", False) is True
                Personality(desktop_mode, public_mode, enabled)
                self.desktop_mouth.set(desktop_mode)
                self.public_mouth.set(public_mode)
                self.public_profanity.set(enabled)
                if data.get("appearance") in PALETTES:
                    self.appearance.set(data["appearance"])
                if data.get("text_size") in ('small', 'default', 'large', 'larger'):
                    self.text_size.set(data["text_size"])
                self.onboarded = data.get("onboarded", True) is True
            except (ValueError, OSError):
                pass

    def save(self, quiet=False):
        try:
            Personality(self.desktop_mouth.get(), self.public_mouth.get(), self.public_profanity.get())
        except ValueError as exc:
            self.show_info("Greeting style not saved", str(exc))
            return
        DATA.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps({
            "auto_start": self.auto_start.get(), "connection": self.connection.get(), "address": self.address.get(),
            "community": self.community.get(), "welcome": self.welcome.get(),
            "desktop_mouth": self.desktop_mouth.get(),
            "public_mouth": self.public_mouth.get(),
            "public_profanity": self.public_profanity.get(),
            "appearance": self.appearance.get(), "text_size": self.text_size.get(),
            "onboarded": self.onboarded,
        }, indent=2), encoding="utf-8")
        if quiet:
            return
        self.log("Settings saved.")
        self.log(Personality(self.desktop_mouth.get(), self.public_mouth.get(), self.public_profanity.get()).message("welcome"))

    def build(self):
        """App shell: sidebar navigation, page header, page area and a status bar."""
        ui, c = self.ui, THEME
        self.shell = tk.Frame(self.root, bg=c['bg'])
        self.shell.pack(fill='both', expand=True)
        sidebar = tk.Frame(self.shell, bg=c['sidebar'], padx=SPACE['md'], pady=SPACE['lg'])
        sidebar.pack(side='left', fill='y')
        self.navigation = sidebar
        self.build_brand(sidebar)
        self.nav_buttons = []
        for group, items in NAV:
            tk.Label(sidebar, text=group.upper(), font=ui.font('small'), fg=c['subtle'], bg=c['sidebar'],
                     anchor='w').pack(fill='x', padx=SPACE['sm'], pady=(SPACE['lg'], SPACE['xs']))
            for page, icon, text in items:
                self.nav_buttons.append(self.nav_item(sidebar, page, icon, text))
        tk.Label(sidebar, text=f'Version {VERSION}', font=ui.font('small'), fg=c['subtle'], bg=c['sidebar'],
                 anchor='w').pack(side='bottom', fill='x', padx=SPACE['sm'])

        main = tk.Frame(self.shell, bg=c['bg'])
        main.pack(side='left', fill='both', expand=True)
        top = tk.Frame(main, bg=c['bg'], padx=SPACE['xl'], pady=SPACE['lg'])
        top.pack(fill='x')
        heading = tk.Frame(top, bg=c['bg'])
        heading.pack(side='left', fill='x', expand=True)
        self.page_title = tk.StringVar(value='Dashboard')
        self.page_subtitle = tk.StringVar(value=PAGE_INFO['HOME'][1])
        tk.Label(heading, textvariable=self.page_title, font=ui.font('display'), fg=c['text'], bg=c['bg'],
                 anchor='w').pack(fill='x')
        tk.Label(heading, textvariable=self.page_subtitle, font=ui.font('small'), fg=c['muted'], bg=c['bg'],
                 anchor='w').pack(fill='x')
        server_area = tk.Frame(top, bg=c['bg'])
        server_area.pack(side='right')
        ui.pill(server_area, self.status, bg='bg', role='body').pack(side='left', padx=(0, SPACE['md']))
        self.server_button = ui.button(server_area, 'Start server', self.toggle_server, 'primary', bg='bg')
        self.server_button.pack(side='left')
        self.status.trace_add('write', self.refresh_status_indicator)

        status_bar = tk.Frame(main, bg=c['surface_alt'], padx=SPACE['xl'], pady=SPACE['xs'] + 2,
                              highlightthickness=1, highlightbackground=c['border'])
        status_bar.pack(side='bottom', fill='x')
        for variable in (self.link_warning, self.mqtt_summary):
            ui.pill(status_bar, variable, bg='surface_alt').pack(side='left', padx=(0, SPACE['xl']))
        tk.Label(status_bar, textvariable=self.uptime, font=ui.font('small'), fg=c['muted'],
                 bg=c['surface_alt']).pack(side='right')

        self.page_host = tk.Frame(main, bg=c['bg'])
        self.page_host.pack(fill='both', expand=True)
        self.page_host.grid_rowconfigure(0, weight=1)
        self.page_host.grid_columnconfigure(0, weight=1)
        self.build_home()
        self.refresh_status_indicator()
        self.select_page('HOME')

    def build_brand(self, parent):
        c, ui = THEME, self.ui
        brand = tk.Frame(parent, bg=c['sidebar'], padx=SPACE['sm'])
        brand.pack(fill='x')
        tk.Label(brand, text='THE', font=ui.font('small'), fg=c['muted'], bg=c['sidebar'], anchor='w').pack(fill='x')
        word = tk.Frame(brand, bg=c['sidebar'])
        word.pack(fill='x')
        for text, colour in (('SIT', 'brand_green'), ('DOWN', 'brand_white'), (' BBS', 'brand_red')):
            tk.Label(word, text=text, font=ui.font('title'), fg=c[colour], bg=c['sidebar']).pack(side='left')
        tk.Label(brand, text='North Jersey Communications', font=ui.font('small'), fg=c['subtle'],
                 bg=c['sidebar'], anchor='w').pack(fill='x')

    def nav_item(self, parent, page, icon, text):
        c = THEME
        control = tk.Label(parent, text=f'{icon}   {text}', font=self.ui.font('nav'), fg=c['muted'], bg=c['sidebar'],
                           anchor='w', padx=SPACE['md'], pady=SPACE['sm'] - 1, cursor='hand2', takefocus=True,
                           highlightthickness=2, highlightbackground=c['sidebar'], highlightcolor=c['focus'])
        control.pack(fill='x', pady=1)
        control.page_name, control.base_text = page, f'{icon}   {text}'
        go = lambda _e=None: (self.select_page(page), 'break')[1]
        for event in ('<Button-1>', '<Return>', '<space>'):
            control.bind(event, go)
        control.bind('<Enter>', lambda _e: control.configure(fg=c['text']))
        control.bind('<Leave>', lambda _e: control.configure(
            fg=c['text'] if self.current_page == page else c['muted']))
        control.bind('<FocusIn>', lambda _e: control.configure(highlightbackground=c['focus']))
        control.bind('<FocusOut>', lambda _e: control.configure(highlightbackground=control.cget('bg')))
        return control

    def bind_shortcuts(self):
        """⌘1–⌘9 jump between pages, ⌘, opens Settings and ⌘F searches the activity log."""
        modifier = 'Command' if sys.platform == 'darwin' else 'Control'
        order = [page for _group, items in NAV for page, _icon, _text in items]
        for number, page in enumerate(order[:9], start=1):
            self.root.bind_all(f'<{modifier}-Key-{number}>', lambda _e, p=page: self.select_page(p))
        self.root.bind_all(f'<{modifier}-comma>', lambda _e: self.select_page('SETTINGS'))
        self.root.bind_all(f'<{modifier}-f>', lambda _e: self.focus_log_search())

    def focus_log_search(self):
        self.select_page('HOME')
        if getattr(self, 'log_search', None) is not None and self.log_search.winfo_exists():
            self.log_search.focus_set()

    def apply_appearance(self):
        """Switch dark/light by rebuilding the window; state, log text and the open page are kept."""
        mode = self.appearance.get()
        if mode not in PALETTES or mode == self.ui.mode:
            return
        log_text = self.output.get('1.0', 'end-1c')
        page = self.current_page
        window = getattr(self, '_expanded_log_window', None)
        if window is not None and window.winfo_exists():
            window.destroy()
        if self.active_page is not None:
            self.active_page.destroy()
            self.active_page = None
        self.shell.destroy()
        apply_theme_tokens(mode)
        self.ui = UI(self.root, mode, self.text_size.get())
        self.root.configure(bg=THEME['bg'])
        self.chat_tabs, self.chat_views, self.chat_tab_frames, self.wire_views = None, {}, {}, {}
        self.build()
        self.output.configure(state='normal')
        self.output.delete('1.0', 'end')
        self.output.insert('1.0', log_text + '\n')
        self.output.configure(state='disabled')
        self.output.see('end')
        self.select_page(page)
        self.save(quiet=True)

    def apply_text_size(self):
        self.ui.apply_text_size(self.text_size.get())
        self.save(quiet=True)

    # ------------------------------------------------------------------ home

    def build_home(self):
        ui, c = self.ui, THEME
        self.home_page = tk.Frame(self.page_host, bg=c['bg'])
        self._top_height = None
        self.home_page.grid(row=0, column=0, sticky='nsew')
        self.home_split = tk.PanedWindow(self.home_page, orient='vertical', bg=c['bg'], sashwidth=SPACE['sm'],
                                         sashrelief='flat', bd=0, showhandle=False)
        top = tk.Frame(self.home_split, bg=c['bg'], padx=SPACE['xl'])
        if not self.onboarded:
            self.build_onboarding(top)
        cards = tk.Frame(top, bg=c['bg'])
        cards.pack(fill='both', expand=True)
        for column in range(3):
            cards.grid_columnconfigure(column, weight=1, uniform='home')

        server_card, body = ui.card(cards, 'BBS server', 'Runs the bulletin board on your radio.')
        server_card.grid(row=0, column=0, sticky='nsew', padx=(0, SPACE['sm']))
        ui.pill(body, self.status, role='heading').pack(fill='x')
        for variable in (self.uptime, self.node_count, self.traffic):
            tk.Label(body, textvariable=variable, font=ui.font('small'), fg=c['muted'], bg=c['surface'],
                     anchor='w').pack(fill='x')
        actions = tk.Frame(body, bg=c['surface'])
        actions.pack(fill='x', pady=(SPACE['md'], SPACE['sm']))
        ui.button(actions, 'Start', self.start, 'primary', small=True).pack(side='left')
        ui.button(actions, 'Restart', self.restart, small=True).pack(side='left', padx=SPACE['xs'])
        ui.button(actions, 'Stop', self.confirm_stop, 'danger', small=True).pack(side='left')
        ui.toggle(body, 'Start when the app opens', self.auto_start)
        ui.toggle(body, 'Restart after a crash', self.auto_restart)
        ui.toggle(body, 'Demo mode (sample data, sends nothing)', self.demo)

        radio, body = ui.card(cards, 'Radio connection', 'How this computer reaches your Meshtastic radio.')
        radio.grid(row=0, column=1, sticky='nsew', padx=SPACE['sm'])
        ui.segmented(body, self.connection, (('USB cable', 'serial'), ('Network (Wi-Fi)', 'tcp'))).pack(fill='x')
        self.address_label = tk.StringVar()
        tk.Label(body, textvariable=self.address_label, font=ui.font('small'), fg=c['muted'], bg=c['surface'],
                 anchor='w').pack(fill='x', pady=(SPACE['md'], 2))
        ui.entry(body, self.address).pack(fill='x')
        buttons = tk.Frame(body, bg=c['surface'])
        buttons.pack(fill='x', pady=(SPACE['sm'], 0))
        ui.button(buttons, 'Find USB Ports', self.list_ports, small=True).pack(side='left')
        ui.button(buttons, 'Check Radio Connection', self.check_radio_connection, small=True).pack(
            side='left', padx=SPACE['xs'])
        self.connection_summary = tk.StringVar()
        ui.pill(body, self.link_warning).pack(fill='x', pady=(SPACE['md'], 0))
        tk.Label(body, textvariable=self.radio_activity, font=ui.font('small'), fg=c['muted'], bg=c['surface'],
                 anchor='w', justify='left', wraplength=300).pack(fill='x')
        ui.notice(body, 'A network check briefly uses the radio\'s single app connection. '
                  'Close other Meshtastic apps first.', wrap=280).pack(fill='x', pady=(SPACE['sm'], 0))
        for variable in (self.connection, self.address):
            variable.trace_add('write', self.refresh_connection_summary)
        self.refresh_connection_summary()

        networks, body = ui.card(cards, 'MQTT networks', 'Optional internet links to CentralNJ and SJMesh.')
        networks.grid(row=0, column=2, sticky='nsew', padx=(SPACE['sm'], 0))
        for network, label in NETWORK_LABELS.items():
            row = tk.Frame(body, bg=c['surface'])
            row.pack(fill='x', pady=(0, SPACE['sm']))
            tk.Label(row, text=label, font=ui.font('body'), fg=c['text'], bg=c['surface'], anchor='w').pack(fill='x')
            ui.pill(row, self.mqtt_states[network]).pack(fill='x')
            line = tk.Frame(row, bg=c['surface'])
            line.pack(fill='x', pady=(SPACE['xs'], 0))
            ui.button(line, 'Connect', lambda n=network: self.mqtt_network_action(n, True), small=True).pack(side='left')
            ui.button(line, 'Disconnect', lambda n=network: self.mqtt_network_action(n, False), 'ghost',
                      small=True).pack(side='left', padx=SPACE['xs'])
        ui.button(body, 'View network activity', lambda: self.select_page('WIRE'), 'ghost',
                  small=True).pack(anchor='w', pady=(SPACE['sm'], 0))

        log_section = tk.Frame(self.home_split, bg=c['bg'], padx=SPACE['xl'], pady=SPACE['md'])
        log_card, log_body = ui.card(log_section)
        log_card.pack(fill='both', expand=True)
        log_header = tk.Frame(log_body, bg=c['surface'])
        log_header.pack(fill='x', pady=(0, SPACE['sm']))
        ui.label(log_header, 'Activity log', 'heading', bg='surface').pack(side='left')
        for text, command in (('Clear', self.clear_log), ('Save…', self.export_log), ('Expand', self.expand_log)):
            ui.button(log_header, text, command, 'ghost', small=True).pack(side='right', padx=(SPACE['xs'], 0))
        self.log_query = tk.StringVar()
        ui.button(log_header, 'Find', self.find_log, small=True).pack(side='right', padx=(SPACE['xs'], SPACE['sm']))
        self.log_search = ui.entry(log_header, self.log_query, width=22)
        self.log_search.pack(side='right')
        self.log_search.bind('<Return>', lambda _e: self.find_log())
        self.output = tk.Text(log_body, bg=c['log_bg'], fg=c['log_text'], insertbackground=c['text'], wrap='word',
                              relief='flat', bd=0, state='disabled', height=12, font=ui.font('mono'),
                              padx=SPACE['md'], pady=SPACE['sm'], highlightthickness=1,
                              highlightbackground=c['border'], selectbackground=c['selection'])
        scroll = ttk.Scrollbar(log_body, command=self.output.yview)
        scroll.pack(side='right', fill='y')
        self.output.configure(yscrollcommand=scroll.set)
        self.output.pack(fill='both', expand=True)
        self.output.tag_configure('event_error', foreground=c['danger'])
        self.output.tag_configure('event_ready', foreground=c['success'])

        self.home_split.pack(fill='both', expand=True)
        top.update_idletasks()
        linespace = self.output.tk.call('font', 'metrics', self.output.cget('font'), '-linespace')
        self.home_split.add(top, minsize=120, stretch='never')
        self.home_split.add(log_section, stretch='always',
                            minsize=log_header.winfo_reqheight() + LOG_MIN_LINES * int(linespace) + 60)
        log_minimum = log_header.winfo_reqheight() + LOG_MIN_LINES * int(linespace) + 60

        def fit_top(_event=None):
            """Give the cards the height their wrapped text needs, but never squeeze the log below its minimum."""
            if not top.winfo_exists():
                return
            room = self.home_split.winfo_height() - log_minimum - SPACE['sm']
            height = top.winfo_reqheight() if room <= 0 else min(top.winfo_reqheight(), room)
            if height != getattr(self, '_top_height', None):
                self._top_height = height
                self.home_split.paneconfigure(top, height=height)
        cards.bind('<Configure>', lambda _e: self.root.after_idle(fit_top))
        self.home_split.bind('<Configure>', lambda _e: self.root.after_idle(fit_top))
        if not getattr(self, '_greeted', False):
            self._greeted = True
            self.log(Personality(self.desktop_mouth.get(), self.public_mouth.get(),
                                 self.public_profanity.get()).message('welcome'))
            self.log(f'The Sitdown BBS {VERSION}')
            self.log('Pick a radio connection and press Start, or turn on Demo mode to look around.')

    def build_onboarding(self, parent):
        """A one-line first-run guide; dismissed for good with one click."""
        ui, c = self.ui, THEME
        card = tk.Frame(parent, bg=c['surface'], highlightthickness=1, highlightbackground=c['info'],
                        padx=SPACE['lg'], pady=SPACE['sm'])
        card.pack(fill='x', pady=(0, SPACE['md']))
        ui.label(card, 'New here?', 'heading', bg='surface').pack(side='left')
        ui.label(card, '  Choose how your radio connects, press Check Radio Connection, then Start.', 'body', 'muted',
                 bg='surface').pack(side='left')

        def demo():
            self.demo.set(True)
            self.start()

        def dismiss():
            self.onboarded = True
            self.save(quiet=True)
            card.destroy()
        ui.button(card, 'Dismiss', dismiss, 'ghost', small=True).pack(side='right')
        ui.button(card, 'Try demo mode', demo, small=True).pack(side='right', padx=SPACE['xs'])

    def surface_key(self, widget):
        colour = str(widget.cget('bg'))
        return next((key for key in ('surface', 'bg', 'surface_alt', 'sidebar') if THEME.get(key) == colour), 'surface')

    def toggle_server(self):
        if self.supervisor and self.supervisor.running or str(self.status.get()).startswith('Demo running'):
            self.confirm_stop()
        else:
            self.start()

    def confirm_stop(self):
        """Stopping a live BBS ends every radio session, so ask first; demo and idle stop at once."""
        live = bool(self.supervisor and self.supervisor.running and not self.demo.get())
        if live and not messagebox.askyesno('Stop the BBS server?',
                                            'People using your BBS over the radio will stop getting replies '
                                            'until you start it again.', parent=self.root):
            return
        self.stop()

    def toolbar(self, page):
        bar = tk.Frame(page, bg=THEME['bg'])
        bar.pack(fill='x', pady=(0, SPACE['sm']))
        return bar

    # ------------------------------------------------------------------ pages

    def copy_text(self, value, note):
        self.root.clipboard_clear()
        self.root.clipboard_append(value)
        self.log(note + ' ' + value)

    def wire_page(self, page):
        """MQTT network activity: broker status and controls, then per-network packet feeds."""
        ui, c = self.ui, THEME
        card, body = ui.card(page, 'Brokers', 'MQTT links are separate from your radio. Packets here are never '
                             'forwarded to the radio.', actions=[('Show details', self.mqtt_network_status, 'ghost')])
        card.pack(fill='x', pady=(0, SPACE['md']))
        row = tk.Frame(body, bg=c['surface'])
        row.pack(fill='x')
        for network, label in NETWORK_LABELS.items():
            cell = tk.Frame(row, bg=c['surface'])
            cell.pack(side='left', fill='x', expand=True)
            tk.Label(cell, text=label, font=ui.font('body'), fg=c['text'], bg=c['surface'], anchor='w').pack(fill='x')
            ui.pill(cell, self.mqtt_states[network]).pack(fill='x')
            line = tk.Frame(cell, bg=c['surface'])
            line.pack(fill='x', pady=(SPACE['xs'], 0))
            ui.button(line, 'Connect', lambda n=network: self.mqtt_network_action(n, True), small=True).pack(side='left')
            ui.button(line, 'Disconnect', lambda n=network: self.mqtt_network_action(n, False), 'ghost',
                      small=True).pack(side='left', padx=SPACE['xs'])
        wire_tabs = ttk.Notebook(page)
        wire_tabs.pack(fill='both', expand=True)
        self.wire_views = {}
        for network, label in NETWORK_LABELS.items():
            frame = tk.Frame(wire_tabs, bg=c['surface'], padx=SPACE['md'], pady=SPACE['md'])
            wire_tabs.add(frame, text=label)
            self.wire_views[network] = self.page_text(frame, self.render_wire(network))
        counts = tk.Frame(wire_tabs, bg=c['surface'], padx=SPACE['md'], pady=SPACE['md'])
        wire_tabs.add(counts, text='BBS server counter')
        data = self.snapshot['mqtt']
        self.page_text(counts, f"Status: {data['status']}\nMessages this server session: {data['total']}\n\n"
                       + '\n'.join(f'{count}  {topic}' for topic, count in data['topics'])
                       + '\n\nThis older counter runs inside the BBS server (Settings → Advanced). '
                       'It excludes retained messages and is not proof of radio reception.')

    def radio_page(self, page):
        ui, c = self.ui, THEME
        cards = tk.Frame(page, bg=c['bg'])
        cards.pack(fill='both', expand=True)
        cards.grid_columnconfigure(0, weight=1, uniform='radio')
        cards.grid_columnconfigure(1, weight=1, uniform='radio')
        status, body = ui.card(cards, 'Connection', 'What the app knows about your radio right now.',
                               actions=[('Change on Dashboard', lambda: self.select_page('HOME'), 'ghost')])
        status.grid(row=0, column=0, sticky='nsew', padx=(0, SPACE['sm']))
        ui.pill(body, self.status, role='heading').pack(fill='x')
        ui.pill(body, self.link_warning).pack(fill='x', pady=(SPACE['sm'], 0))
        for variable in (self.connection_summary, self.radio_activity, self.node_count, self.uptime):
            tk.Label(body, textvariable=variable, font=ui.font('body'), fg=c['muted'], bg=c['surface'], anchor='w',
                     justify='left').pack(fill='x', pady=(SPACE['xs'], 0))
        tk.Label(body, textvariable=self.centralnj_status, font=ui.font('small'), fg=c['muted'], bg=c['surface'],
                 justify='left', anchor='w').pack(fill='x', pady=(SPACE['md'], 0))
        ui.notice(body, 'A running server does not prove a continuous radio link. Radio activity shows the last '
                  'time a packet was heard.', wrap=380).pack(fill='x', pady=(SPACE['md'], 0))
        channels, body = ui.card(cards, 'Channels', 'Read from the radio when the BBS server starts.')
        channels.grid(row=0, column=1, sticky='nsew', padx=(SPACE['sm'], 0))
        rows = [ch for ch in self.snapshot.get('channels') or [] if isinstance(ch, dict)]
        if not rows:
            ui.empty_state(body, 'No channels loaded', 'Start the BBS server to read the channel list from your radio.',
                           ('Start server', self.start)).pack(fill='x')
        for ch in rows:
            line = tk.Frame(body, bg=c['surface'])
            line.pack(fill='x', pady=2)
            tk.Label(line, text=str(ch.get('index')), font=ui.font('mono'), fg=c['muted'], bg=c['surface'], width=3,
                     anchor='w').pack(side='left')
            tk.Label(line, text=str(ch.get('name')), font=ui.font('body'), fg=c['text'], bg=c['surface'],
                     anchor='w').pack(side='left')

    def about_page(self, page):
        ui, c = self.ui, THEME
        card, body = ui.card(page)
        card.pack(fill='x')
        word = tk.Frame(body, bg=c['surface'])
        word.pack(anchor='w')
        for text, colour in (('THE ', 'muted'), ('SIT', 'brand_green'), ('DOWN', 'brand_white'), (' BBS', 'brand_red')):
            tk.Label(word, text=text, font=ui.font('display'), fg=c[colour], bg=c['surface']).pack(side='left')
        ui.label(body, f'Version {VERSION}  ·  North Jersey Communications Division', 'small', 'muted',
                 bg='surface').pack(fill='x')
        ui.label(body, 'A Jersey-flavoured Meshtastic bulletin board: mail, boards, chat and announcements for '
                 'people on your mesh, run from this computer through your radio.', 'body', bg='surface',
                 wrap=720).pack(fill='x', pady=(SPACE['md'], 0))
        ui.section(body, 'Credits')
        ui.label(body, 'Based on TC²-BBS by TheCommsChannel (github.com/TheCommsChannel/TC2-BBS-mesh). '
                 'Licensed GPL-3.0. Weather by Open-Meteo, only when you turn it on.', 'small', 'muted',
                 bg='surface', wrap=720).pack(fill='x')
        ui.section(body, 'Good to know')
        for line in ('MQTT activity is separate from what your radio hears.',
                     'Demo mode and build checks do not prove a radio will work; test with real hardware.',
                     'Backups can contain messages and keys. Keep them private.'):
            ui.notice(body, line, wrap=700).pack(fill='x', pady=1)
        actions = tk.Frame(body, bg=c['surface'])
        actions.pack(fill='x', pady=(SPACE['md'], 0))
        ui.button(actions, 'Copy diagnostics', self.copy_diagnostics, small=True).pack(side='left')
        ui.button(actions, 'Keyboard shortcuts', self.show_shortcuts, 'ghost', small=True).pack(side='left',
                                                                                              padx=SPACE['xs'])

    def show_shortcuts(self):
        key = '⌘' if sys.platform == 'darwin' else 'Ctrl+'
        order = [text for _group, items in NAV for _page, _icon, text in items][:9]
        self.show_info('Keyboard shortcuts', '\n'.join(f'{key}{n}   {text}' for n, text in enumerate(order, 1))
                       + f'\n{key},   Settings\n{key}F   Search the activity log\n'
                       'Tab moves between controls; Space or Return presses the focused one.')

    def greeting_settings(self, frame):
        ui, c = self.ui, THEME
        ui.section(frame, 'Desktop greeting')
        ui.label(frame, 'How the app greets you in the activity log.', 'small', 'muted', bg='surface').pack(fill='x')
        ui.segmented(frame, self.desktop_mouth, (('Clean', 'clean'), ('Jersey', 'attitude'),
                                                 ('Heavy', 'uncensored'))).pack(anchor='w', pady=(SPACE['xs'], 0))
        preview = tk.StringVar()

        def refresh(*_):
            preview.set(Personality(self.desktop_mouth.get()).message('welcome'))
        self.desktop_mouth.trace_add('write', refresh)
        refresh()
        tk.Label(frame, textvariable=preview, font=ui.font('mono'), fg=c['text'], bg=c['field'], wraplength=560,
                 justify='left', anchor='nw', padx=SPACE['md'], pady=SPACE['sm'], highlightthickness=1,
                 highlightbackground=c['border']).pack(fill='x', pady=(SPACE['sm'], 0))
        ui.button(frame, 'Save greeting style', self.save, small=True).pack(anchor='w', pady=(SPACE['sm'], 0))
        self.greeting_preview = preview

    def update_centralnj_status(self, radio_state='unverified', mqtt_state='disabled'):
        """Update read-only operator status without claiming delivery."""
        text = format_operator_status(radio_state, mqtt_state)
        if self.centralnj_status.get() != text:
            self.centralnj_status.set(text)

    def toggle_row(self, parent, title, variable):
        """Compatibility wrapper: a design-system switch on a surface."""
        return self.ui.toggle(parent, title, variable, bg=self.surface_key(parent))

    def config_path(self):
        return (DATA if getattr(sys, 'frozen', False) else ROOT) / 'config.ini'

    def read_config(self):
        config = configparser.ConfigParser(interpolation=None)
        path = self.config_path()
        config.read(path if path.exists() else ROOT / 'example_config.ini')
        return config

    def control_request(self, action, **fields):
        if self.demo.get():
            self.show_info('Demo mode', 'Demonstration only — no radio or internet request was sent.')
            return
        try:
            if not self.supervisor:
                raise RuntimeError('Start the BBS server first.')
            self.supervisor.request(action, **fields)
            self.log('Requested ' + action + '; awaiting server response.')
        except RuntimeError as exc:
            self.show_info('The Sitdown', str(exc))

    def select_page(self, title):
        self.current_page = title
        for button in self.nav_buttons:
            chosen = button.page_name == title
            button.configure(bg=THEME['nav_selected'] if chosen else THEME['sidebar'],
                             fg=THEME['text'] if chosen else THEME['muted'],
                             highlightbackground=THEME['nav_selected'] if chosen else THEME['sidebar'])
        name, subtitle = PAGE_INFO.get(title, (title.title(), ''))
        self.page_title.set(name)
        self.page_subtitle.set(subtitle)
        if self.active_page is not None:
            self.active_page.destroy()
            self.active_page = None
        if title == 'HOME':
            self.home_page.grid(row=0, column=0, sticky='nsew')
            self.home_page.tkraise()
            return
        self.home_page.grid_remove()
        page = tk.Frame(self.page_host, bg=THEME['bg'], padx=SPACE['xl'], pady=SPACE['xs'])
        page.grid(row=0, column=0, sticky='nsew')
        self.active_page = page
        builders = {'BBS': self.utility_page, 'NODES': self.nodes_page, 'SETTINGS': self.integration_settings,
                    'CHAT': self.chat_page, 'RADIO': self.radio_page, 'WIRE': self.wire_page,
                    'ABOUT': self.about_page}
        if title in ('MESSAGES', 'BOARDS'):
            self.database_page(page, title)
        else:
            builders.get(title, self.about_page)(page)

    def page_text(self, parent, text):
        return self.ui.text_view(parent, text, bg=self.surface_key(parent))

    def utility_page(self, page):
        """Broadcast: announcements, weather and the radio command list."""
        ui, c = self.ui, THEME
        notebook = ttk.Notebook(page)
        notebook.pack(fill='both', expand=True)
        announcement, weather, commands = (tk.Frame(notebook, bg=c['surface'], padx=SPACE['xl'], pady=SPACE['lg'])
                                           for _ in range(3))
        for frame, title in ((announcement, 'Announcement'), (weather, 'Weather desk'), (commands, 'Radio commands')):
            notebook.add(frame, text=title)
        self.broadcast_tabs = notebook
        ui.label(announcement, 'Send an announcement', 'heading', bg='surface').pack(fill='x')
        ui.label(announcement, 'Goes out once to everyone on the chosen radio channel.', 'small', 'muted',
                 bg='surface').pack(fill='x', pady=(0, SPACE['md']))
        channel = tk.StringVar()
        options = [f"{ch['index']}: {ch['name']}" for ch in self.snapshot['channels']]
        if options:
            channel.set(options[0])
        ui.label(announcement, 'Channel', 'small', 'muted', bg='surface').pack(fill='x')
        ttk.Combobox(announcement, textvariable=channel, values=options, state='readonly').pack(fill='x', pady=(2, SPACE['md']))
        if not options:
            ui.notice(announcement, 'No channels yet. Start the BBS server so the app can read them from your radio.',
                      'warning').pack(fill='x', pady=(0, SPACE['md']))
        ui.label(announcement, 'Message', 'small', 'muted', bg='surface').pack(fill='x')
        text = tk.Text(announcement, height=5, bg=c['field'], fg=c['text'], insertbackground=c['text'], relief='flat',
                       font=ui.font('body'), padx=SPACE['sm'], pady=SPACE['sm'], highlightthickness=1,
                       highlightbackground=c['border'], highlightcolor=c['focus'], wrap='word')
        text.pack(fill='x', pady=(2, 0))
        counter = tk.StringVar(value='0 / 200 bytes')
        tk.Label(announcement, textvariable=counter, font=ui.font('small'), fg=c['muted'], bg=c['surface'],
                 anchor='e').pack(fill='x')
        text.bind('<KeyRelease>', lambda _e: counter.set(f"{len(text.get('1.0', 'end-1c').encode('utf-8'))} / 200 bytes"))

        def send():
            message = text.get('1.0', 'end-1c')
            if not channel.get() or not message.strip() or len(message.encode('utf-8')) > 200:
                self.show_info('Check announcement', 'Choose an enabled channel and enter 1–200 UTF-8 bytes.')
                return
            if messagebox.askyesno('Broadcast announcement?', f'Channel {channel.get()}\n\n{message}', parent=self.root):
                self.control_request('announce', channel=int(channel.get().split(':', 1)[0]), text=message)
        ui.button(announcement, 'Preview & broadcast…', send, 'primary').pack(anchor='w', pady=(SPACE['sm'], 0))
        ui.notice(announcement, 'People can also send /announce over the radio if their node is listed '
                  'under Settings → Admins.').pack(fill='x', pady=(SPACE['lg'], 0))

        config = self.read_config()
        enabled = config.getboolean('weather', 'internet_enabled', fallback=False)
        head = tk.Frame(weather, bg=c['surface'])
        head.pack(fill='x')
        ui.label(head, 'Weather desk', 'heading', bg='surface').pack(side='left')
        ui.button(head, 'Get weather report', lambda: self.control_request('weather'), 'primary',
                  small=True).pack(side='right')
        ui.label(weather, 'Internet weather is ' + ('on (Open-Meteo, cached ten minutes).' if enabled else
                 'off. Turn it on under Settings → Weather.'), 'small', 'muted', bg='surface').pack(fill='x')
        self.page_text(weather, config.get('weather', 'local_report', fallback='') or
                       'No offline report yet. Add one under Settings → Weather so /weather works without internet.')

        ui.label(commands, 'Commands people can send to your BBS', 'heading', bg='surface').pack(fill='x')
        ui.label(commands, 'They send these as a direct message to your BBS radio.', 'small', 'muted',
                 bg='surface').pack(fill='x', pady=(0, SPACE['md']))
        grid = tk.Frame(commands, bg=c['surface'])
        grid.pack(fill='x')
        grid.grid_columnconfigure(1, weight=1)
        for row, (command, description) in enumerate(COMMANDS):
            tk.Label(grid, text=command, font=ui.font('mono'), fg=c['text'], bg=c['surface'], anchor='w').grid(
                row=row, column=0, sticky='w', pady=2, padx=(0, SPACE['lg']))
            tk.Label(grid, text=description, font=ui.font('body'), fg=c['muted'], bg=c['surface'], anchor='w').grid(
                row=row, column=1, sticky='w')
            ui.button(grid, 'Copy', lambda value=command.split()[0]: self.copy_text(value, 'Command copied.'),
                      'ghost', small=True).grid(row=row, column=2, sticky='e')

    def nodes_page(self, page):
        """Every node the app has learned about, not just the radio's limited list."""
        ui, c = self.ui, THEME
        query = tk.StringVar()
        period = tk.StringVar(value='day')
        source = tk.StringVar(value='all')
        bar = self.toolbar(page)
        search = ui.entry(bar, query, width=32)
        search.pack(side='left')
        ui.segmented(bar, period, (('1 hour', 'hour'), ('24 hours', 'day'), ('7 days', 'week'), ('All', 'all')),
                     bg='bg').pack(side='left', padx=(SPACE['md'], 0))
        ui.segmented(bar, source, (('Anywhere', 'all'), ('Radio', 'radio'), ('MQTT', 'mqtt')),
                     bg='bg').pack(side='left', padx=(SPACE['sm'], 0))
        count = tk.StringVar()
        tk.Label(page, textvariable=count, font=ui.font('small'), fg=c['muted'], bg=c['bg'], anchor='w').pack(fill='x')
        box = self.page_text(page, '')
        spans = {'hour': 3600, 'day': 86400, 'week': 7 * 86400, 'all': None}

        def refresh(*_):
            now = int(time.time())
            span = spans[period.get()]
            radio = [row for row in self.snapshot.get('nodes') or [] if isinstance(row, dict)]
            radio_ids = {row.get('id') for row in radio}
            try:
                total, rows = self.directory().search(query.get(), None if span is None else now - span,
                                                      source.get(), limit=500)
                saved = self.directory().count()
            except sqlite3.Error as exc:
                set_text(box, 'Could not read the node list: ' + str(exc))
                return
            shown = f'Showing {len(rows)} of {total}' if total > len(rows) else f'{total} node{"s" if total != 1 else ""}'
            count.set(f'{shown} · {saved} saved on this Mac · your radio holds {len(radio)} '
                      '(radios forget the oldest when full; this list keeps them)')
            if rows:
                set_text(box, '\n\n'.join(format_entry(row, now, radio_ids) for row in rows))
            elif saved:
                set_text(box, 'No nodes match. Try a longer time range or a different search.')
            else:
                set_text(box, 'No nodes yet. Start the BBS server or connect an MQTT network, and nodes '
                              'will collect here as they are heard.')
        for variable in (query, period, source):
            variable.trace_add('write', refresh)
        refresh()
        search.focus_set()

    def database_page(self, page, title):
        ui, c = self.ui, THEME
        bar = self.toolbar(page)
        what = 'bulletins' if title == 'BOARDS' else 'mail'
        ui.label(bar, f'Latest 100 {what} stored by your BBS. Read only; people use radio commands to post.',
                 'small', 'muted').pack(side='left')
        ui.button(bar, 'Refresh', lambda: self.select_page(title), 'ghost', small=True, bg='bg').pack(side='right')
        path = self.config_path().parent / 'bulletins.db'
        records, problem = [], None
        if self.demo.get():
            records = ([('Welcome to the Social Club', 'General · DEMO · today', 'Stay local, stay connected.')]
                       if title == 'BOARDS' else [('Sample mail', 'From DEMO to you · today', 'Ayyy, pull up a chair.')])
        elif path.exists():
            try:
                with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
                    if title == 'BOARDS':
                        rows = db.execute('SELECT board, sender_short_name, date, subject, content FROM bulletins '
                                          'ORDER BY id DESC LIMIT 100').fetchall()
                        records = [(subject, f'{board} · {sender} · {date}', content)
                                   for board, sender, date, subject, content in rows]
                    else:
                        rows = db.execute('SELECT sender_short_name, recipient, date, subject, content FROM mail '
                                          'ORDER BY id DESC LIMIT 100').fetchall()
                        records = [(subject, f'From {sender} to {recipient} · {date}', content)
                                   for sender, recipient, date, subject, content in rows]
            except sqlite3.Error as exc:
                problem = 'Could not read the BBS database: ' + str(exc)
        holder = tk.Frame(page, bg=c['surface'], padx=SPACE['lg'], pady=SPACE['md'], highlightthickness=1,
                          highlightbackground=c['border'])
        holder.pack(fill='both', expand=True)
        if problem:
            ui.notice(holder, problem, 'error').pack(fill='x')
            return
        if not records:
            first = not path.exists() and not self.demo.get()
            ui.empty_state(holder, f'No {what} yet',
                           'Start the BBS server once to create its database.' if first else
                           f'When people post {what} over the radio, it shows up here.',
                           ('Start server', self.start) if first else None).pack(fill='x')
            return
        box = ui.text_view(holder, '')
        box.configure(state='normal')
        for subject, meta, content in records:
            box.insert('end', f'{subject}\n', 'heading')
            box.insert('end', f'{meta}\n', 'muted')
            box.insert('end', f'{content}\n\n')
        box.configure(state='disabled')

    # ------------------------------------------------------------------ chat

    def chat_channel_names(self):
        """Radio channels from the last snapshot plus any channel that has messages."""
        names = {ch['index']: ch['name'] for ch in self.snapshot.get('channels', [])
                 if isinstance(ch, dict) and isinstance(ch.get('index'), int)}
        for index in self.chat_log.channels():
            names.setdefault(index, f'Channel {index}')
        return dict(sorted(names.items())) or {0: 'Primary'}

    def node_names(self):
        """Known radio nodes from the last snapshot: id -> (short, long)."""
        return {row['id']: (str(row.get('shortName') or ''), str(row.get('longName') or ''))
                for row in self.snapshot.get('nodes') or [] if isinstance(row, dict) and isinstance(row.get('id'), str)}

    def dm_tab_name(self, peer):
        short, _long = self.node_names().get(peer, ('', ''))
        return '✉ ' + (short or peer)

    def chat_tab_list(self):
        """(key, tab name) for each channel, then each direct conversation (this session's first)."""
        tabs = list(self.chat_channel_names().items())
        peers = list(dict.fromkeys(self.dm_peers + self.chat_log.peers()))
        keys = list(dict.fromkeys(self.chat_log.rooms() + self.sendable_rooms()))
        rooms = [(key, self.room_tab_name(key)) for key in sorted(keys)]
        return tabs + [(f'dm:{peer}', self.dm_tab_name(peer)) for peer in peers] + rooms

    def sendable_rooms(self):
        """MQTT chat keys the operator turned sending on for (shown even before any message)."""
        config = self.read_config()
        found = []
        for network in NETWORK_LABELS:
            section = 'mqtt_' + network
            try:
                allowed = (config.getboolean(section, 'enabled', fallback=False)
                           and config.getboolean(section, 'allow_publish', fallback=False))
            except ValueError:
                allowed = False
            if allowed:
                keys = keys_for_topics(config.get(section, 'channel_keys', fallback=''),
                                       config.get(section, 'topic', fallback=''))
                found += [f'mqtt:{network}:{room}' for room in send_rooms(config.get(section, 'topic', fallback=''), keys)]
        return found

    @staticmethod
    def room_tab_name(key):
        network, room = mqtt_room(key)
        label = NETWORK_LABELS.get(network, network)
        return '🌐 ' + (label if room == label else f'{label} · {room}')

    def chat_page(self, page):
        ui, c = self.ui, THEME
        bar = self.toolbar(page)
        toggle = tk.Frame(bar, bg=c['bg'])
        toggle.pack(side='left', fill='x', expand=True)
        ui.toggle(toggle, 'Allow sending this session', self.centralnj_send_enabled,
                  'Needed to send chat, direct messages and announcements. Turns off when you quit.', bg='bg')
        ui.button(bar, 'Clear history…', self.clear_chat_history, 'ghost', small=True, bg='bg').pack(side='right')
        ui.button(bar, 'New direct message', self.new_direct_message, 'primary', small=True, bg='bg').pack(
            side='right', padx=SPACE['xs'])
        tabs = ttk.Notebook(page)
        tabs.pack(fill='both', expand=True, pady=(SPACE['xs'], 0))
        self.chat_views = {}
        self.chat_tabs, self.chat_tab_frames = tabs, {}
        sendable = set(self.sendable_rooms())
        for index, name in self.chat_tab_list():
            frame = tk.Frame(tabs, bg=c['surface'], padx=SPACE['md'], pady=SPACE['md'])
            tabs.add(frame, text=name)
            self.chat_tab_frames[index] = frame
            if mqtt_room(index) and index not in sendable:
                network = NETWORK_LABELS.get(mqtt_room(index)[0], '')
                ui.notice(frame, f"Read only. To send here, turn on 'Allow sending' on the {network} Settings tab.").pack(
                    side='bottom', fill='x', pady=(SPACE['sm'], 0))
                box = self.page_text(frame, self.chat_log.render(index, name))
                box.see('end')
                self.chat_views[index] = (name, box)
                continue
            compose = tk.Frame(frame, bg=c['surface'])
            compose.pack(side='bottom', fill='x', pady=(SPACE['sm'], 0))
            draft = tk.StringVar()
            counter = tk.StringVar(value='0/200 bytes')
            entry = ui.entry(compose, draft)
            peer = dm_peer(index)
            if mqtt_room(index):
                send = lambda _event=None, k=index, n=name, d=draft: self.send_mqtt(k, n, d)
            elif peer:
                send = lambda _event=None, p=peer, n=name, d=draft: self.send_dm(p, n, d)
            else:
                send = lambda _event=None, i=index, n=name, d=draft: self.send_chat(i, n, d)
            ui.button(compose, 'Send', send, 'primary', small=True).pack(side='right', padx=(SPACE['xs'], 0))
            if peer:
                ui.button(compose, 'End conversation', lambda p=peer: self.end_conversation(p), 'ghost',
                          small=True).pack(side='right', padx=(SPACE['xs'], 0))
            tk.Label(compose, textvariable=counter, font=ui.font('small'), fg=c['muted'], bg=c['surface']).pack(
                side='right', padx=SPACE['sm'])
            entry.pack(side='left', fill='x', expand=True)
            entry.bind('<Return>', send)
            draft.trace_add('write', lambda *_, d=draft, n=counter: n.set(f"{len(d.get().encode('utf-8'))}/200 bytes"))
            box = self.page_text(frame, self.chat_log.render(index, name))
            box.see('end')
            self.chat_views[index] = (name, box)
        tabs.bind('<<NotebookTabChanged>>', lambda _event: self.mark_visible_chat_read())
        self.mark_visible_chat_read()

    def visible_chat_channel(self):
        """The channel whose tab is on screen, or None when the CHAT page is closed."""
        tabs = self.chat_tabs
        if tabs is None or not tabs.winfo_exists():
            return None
        selected = tabs.select()
        return next((i for i, frame in self.chat_tab_frames.items() if str(frame) == selected), None)

    def note_chat_message(self, event):
        """Count a heard message as unread unless it is ours or its tab is on screen."""
        key = event_key(event)
        if not event.get('mine') and key != self.visible_chat_channel():
            self.chat_unread[key] += 1
            self.update_unread_labels()

    def mark_visible_chat_read(self):
        channel = self.visible_chat_channel()
        if channel is not None:
            self.chat_unread.pop(channel, None)
        self.update_unread_labels()

    def update_unread_labels(self):
        total = sum(self.chat_unread.values())
        for button in getattr(self, 'nav_buttons', []):
            if button.page_name == 'CHAT':
                button.configure(text=button.base_text + (f' ({total})' if total else ''))
        tabs = self.chat_tabs
        if tabs is not None and tabs.winfo_exists():
            for index, (name, _box) in self.chat_views.items():
                count = self.chat_unread.get(index, 0)
                tabs.tab(self.chat_tab_frames[index], text=name + (f' ({count})' if count else ''))

    def log_chat_status(self, sent):
        """Server log line for the radio's reply to one of our chat messages."""
        if sent.get('type') == 'dm':
            name = 'DM ' + self.dm_tab_name(sent['peer'])[2:]
        elif sent.get('type') == 'mqtt':
            name = 'MQTT ' + self.room_tab_name(event_key(sent))[2:]
        else:
            name = 'Chat ' + self.chat_channel_names().get(sent['channel'], f"Channel {sent['channel']}")
        text = sent['text'] if len(sent['text']) <= 40 else sent['text'][:39] + '…'
        self.log(f'{name}: {status_label(sent)} — "{text}"')

    def clear_chat_history(self):
        if messagebox.askyesno('Clear chat history?', 'Delete saved chat messages for every channel '
                               'on this computer? Messages on the mesh are not affected.', parent=self.root):
            self.chat_log.clear()
            self.refresh_chat_views()
            self.log('Chat history cleared.')

    def send_chat(self, index, name, draft):
        """Operator-initiated channel message; the same opt-in gate as other sends."""
        text = draft.get()
        monitor = self.supervisor
        radio_ready = bool(monitor and monitor.running and monitor.link_state == 'sdk_connected')
        allowed, reason = send_preflight(enabled=self.centralnj_send_enabled.get(), confirmed=True,
                                         radio_ready=radio_ready, demo_mode=self.demo.get(), message=text)
        if not allowed:
            self.show_info('Chat not sent', reason)
            return False
        if index not in self.chat_confirmed:
            if not messagebox.askyesno(f'Send on {name}?',
                                       f'Messages you send here go out over the radio to everyone on {name}.\n\n'
                                       'Allow sending on this channel for the rest of this session?', parent=self.root):
                return False
            self.chat_confirmed.add(index)
        self.control_request('chat', channel=index, text=text)
        draft.set('')
        return True

    def send_dm(self, peer, name, draft):
        """Operator direct message; the same opt-in gate as channel chat, plus a per-node confirmation."""
        text = draft.get()
        monitor = self.supervisor
        radio_ready = bool(monitor and monitor.running and monitor.link_state == 'sdk_connected')
        allowed, reason = send_preflight(enabled=self.centralnj_send_enabled.get(), confirmed=True,
                                         radio_ready=radio_ready, demo_mode=self.demo.get(), message=text)
        if not allowed:
            self.show_info('Message not sent', reason)
            return False
        if peer not in self.dm_confirmed:
            if not messagebox.askyesno(f'Message {name[2:]}?',
                                       f'This sends a direct message over the radio to {peer}.\n\n'
                                       'For 10 minutes after each message, their plain replies come to this tab '
                                       'instead of the BBS menu (replies starting with / still reach the BBS).\n\n'
                                       'Allow direct messages to this node for the rest of this session?',
                                       parent=self.root):
                return False
            self.dm_confirmed.add(peer)
        self.control_request('dm', node=peer, text=text)
        draft.set('')
        return True

    def receive_dm(self, event):
        """A conversation reply from the server; new peers get their own tab on the next redraw."""
        if not self.chat_log.add(event):
            return
        peer = event['peer']
        self.log(f'Direct message from {self.dm_tab_name(peer)[2:]} ({peer}) — see CHAT.')
        if peer not in self.dm_peers:
            self.dm_peers.insert(0, peer)
        self.add_chat_tab_if_new(f'dm:{peer}')
        self.note_chat_message(event)

    def dm_result(self, value):
        if value.get('error'):
            self.log('Direct message not sent: ' + value['error'])
            self.show_info('Message not sent', value['error'])
            return
        if value.get('action') == 'dm':
            self.chat_log.add(value.get('chat') or {})
        self.log(value.get('text', 'Done.'))

    def end_conversation(self, peer):
        self.control_request('dm_end', node=peer)

    def open_dm_tab(self, peer):
        """Show (creating if needed) the direct-message tab for one node."""
        if peer not in self.dm_peers:
            self.dm_peers.insert(0, peer)
        self.select_page('CHAT')
        frame = self.chat_tab_frames.get(f'dm:{peer}')
        if frame is not None and self.chat_tabs is not None:
            self.chat_tabs.select(frame)
            self.mark_visible_chat_read()

    def new_direct_message(self):
        """Pick a known node (or type an id) to start a direct conversation."""
        ui, c = self.ui, THEME
        names = self.node_names()
        choices = [f"{short or '?'} — {long or 'unnamed'}  {node}" for node, (short, long) in sorted(
            names.items(), key=lambda item: (item[1][0] or item[0]).casefold())]
        dialog = tk.Toplevel(self.root)
        dialog.title('New direct message')
        dialog.configure(bg=c['bg'], padx=SPACE['xl'], pady=SPACE['lg'])
        dialog.transient(self.root)
        dialog.resizable(False, False)
        ui.label(dialog, 'New direct message', 'title').pack(fill='x')
        ui.label(dialog, 'Choose a node your radio has heard, or type its id (like !1234abcd).', 'small',
                 'muted').pack(fill='x', pady=(2, SPACE['md']))
        choice = tk.StringVar()
        box = ttk.Combobox(dialog, textvariable=choice, values=choices, width=48, font=ui.font('body'))
        box.pack(fill='x')
        box.focus_set()
        problem = tk.StringVar()
        tk.Label(dialog, textvariable=problem, font=ui.font('small'), fg=c['danger'], bg=c['bg'], anchor='w').pack(
            fill='x', pady=(SPACE['xs'], 0))

        def accept(_event=None):
            text = choice.get().strip()
            peer = peer_id(text.split()[-1]) if text else None
            if peer is None:
                problem.set('Pick a node from the list or type an id like !1234abcd.')
                return
            dialog.destroy()
            self.open_dm_tab(peer)

        buttons = tk.Frame(dialog, bg=c['bg'])
        buttons.pack(fill='x', pady=(SPACE['md'], 0))
        ui.button(buttons, 'Open', accept, 'primary', small=True, bg='bg').pack(side='right')
        ui.button(buttons, 'Cancel', dialog.destroy, 'ghost', small=True, bg='bg').pack(side='right', padx=SPACE['xs'])
        box.bind('<Return>', accept)
        dialog.bind('<Escape>', lambda _e: dialog.destroy())
        self.new_dm_dialog = dialog
        return dialog

    def refresh_chat_views(self):
        """Redraw visible chat tabs whose text changed, keeping the reader near the newest line."""
        for index, (name, box) in self.chat_views.items():
            if not box.winfo_exists():
                continue
            text = self.chat_log.render(index, name)
            if box.get('1.0', 'end-1c') == text:
                continue
            at_bottom = box.yview()[1] >= 0.999
            set_text(box, text)
            if at_bottom:
                box.see('end')

    # ------------------------------------------------------------------ settings

    def render_wire(self, network):
        if self.dual_mqtt is None:
            return self.mesh_feed.render(network, status='Not connected')
        return self.mesh_feed.render(network, status=self.dual_mqtt.snapshot()[network]['status'],
                                     stats=self.dual_mqtt.ingest_stats()[network])

    def drain_mqtt_events(self):
        """Move queued broker results into the per-network feed on the Tk thread."""
        if self.dual_mqtt is not None:
            for network, result in self.dual_mqtt.drain_events(limit=50):
                self.mesh_feed.add(network, result)
                self.record_mqtt_node(network, result)
                if getattr(result, 'text', None) is not None and not self.own_mqtt_echo(network, result):
                    self.receive_mqtt_text(network, result)
            self.flush_mqtt_nodes()
        self.note_mqtt_accepted()
        for network, box in self.wire_views.items():
            if not box.winfo_exists():
                continue
            text = self.render_wire(network)
            if box.get('1.0', 'end-1c') == text:
                continue
            top = box.yview()[0]
            box.configure(state='normal')
            box.delete('1.0', 'end')
            box.insert('1.0', text)
            box.configure(state='disabled')
            box.yview_moveto(top)

    def open_node_directory(self):
        """The desktop's own node list beside the other app files; in memory if the file can't be opened."""
        try:
            CONFIG.parent.mkdir(parents=True, exist_ok=True)
            return NodeDirectory(CONFIG.with_name('node-directory.db'))
        except (OSError, sqlite3.Error):
            return NodeDirectory()

    def directory(self):
        found = getattr(self, 'node_directory', None)
        if found is None:
            found = self.node_directory = NodeDirectory()
        return found

    def record_radio_nodes(self, nodes):
        """Merge the radio's (size-limited) node list into the desktop directory and update the counts."""
        nodes = nodes or []
        try:
            self.directory().record_radio(nodes)
            total = self.directory().count()
        except sqlite3.Error:
            total = len(nodes)
        total = max(total, len(nodes))
        self.node_count.set(f'Known nodes: {total}' + (f' (radio holds {len(nodes)})' if total > len(nodes) else ''))

    def record_mqtt_node(self, network, result):
        """Any accepted MQTT packet tells us its sender exists; NODEINFO also gives names."""
        summary = getattr(result, 'summary', None)
        if not getattr(result, 'accepted', False) or summary is None or summary.from_id in (0, 0xFFFFFFFF):
            return
        pending = self.__dict__.setdefault('_mqtt_sightings', {})
        key = (network, summary.from_id)
        short, long = getattr(result, 'short', '') or '', getattr(result, 'long', '') or ''
        before = pending.get(key, ('', '', {}))
        details = {**before[2], **(getattr(result, 'details', None) or {})}
        pending[key] = (short or before[0], long or before[1], details)

    def flush_mqtt_nodes(self):
        """Write this poll's MQTT sightings in one transaction."""
        pending = self.__dict__.get('_mqtt_sightings')
        if not pending:
            return
        self._mqtt_sightings = {}
        try:
            self.directory().record_mqtt_many([(n, number, s, l, d) for (n, number), (s, l, d) in pending.items()])
        except sqlite3.Error:
            pass

    def own_mqtt_echo(self, network, result):
        """Our own sent message coming back from the broker marks it '✓ on broker' instead of a new message."""
        s = result.summary
        key = self.mqtt_pending.get(s.packet_id)
        if key is None or key != f'mqtt:{network}:{s.channel_id}':
            return False
        del self.mqtt_pending[s.packet_id]
        sent = self.chat_log.set_status(s.packet_id, 'posted')
        if sent is not None:
            self.log_chat_status(sent)
        return True

    def note_mqtt_accepted(self):
        """The broker's PUBACK for a message still waiting on its echo: '✓ broker accepted'."""
        sender = getattr(self, 'mqtt_sender', None)
        if sender is None:
            return
        for packet_id in sender.accepted():
            sent = self.chat_log.set_status(packet_id, 'accepted')
            if sent is not None:
                self.log_chat_status(sent)

    def send_mqtt(self, key, name, draft):
        """Operator text to an MQTT channel: every gate in MQTTSender, plus a confirmation once per channel."""
        network, room = mqtt_room(key)
        text = draft.get()
        if self.demo.get():
            self.show_info('Message not sent', 'Demo mode: transmissions disabled')
            return False
        if not text.strip() or len(text.encode('utf-8')) > 200:
            self.show_info('Message not sent', 'Message must contain 1–200 UTF-8 bytes.')
            return False
        if key not in self.mqtt_confirmed:
            if not messagebox.askyesno(f'Send on {name[2:]}?',
                                       f'This publishes your message, encrypted with the {room} key, to the '
                                       f'{NETWORK_LABELS[network]} MQTT broker. Radios whose gateways downlink '
                                       f'{room} will hear it.\n\nAllow sending here for the rest of this session?',
                                       parent=self.root):
                return False
            self.mqtt_confirmed.add(key)
        monitor = self.dual_mqtt.monitors.get(network) if self.dual_mqtt is not None else None
        try:
            packet_id, sender = self.mqtt_sender.send(
                self.read_config(), network, room, text, client=getattr(monitor, 'client', None),
                sender=self.snapshot.get('me') or getattr(self, 'radio_node', None), demo=self.demo.get(), session_confirmed=True)
        except ValueError as exc:
            self.log(f'{NETWORK_LABELS[network]} {room}: message not sent — {exc}')
            self.show_info('Message not sent', str(exc))
            return False
        self.mqtt_pending[packet_id] = key
        while len(self.mqtt_pending) > 100:
            self.mqtt_pending.pop(next(iter(self.mqtt_pending)))
        self.chat_log.add({'type': 'mqtt', 'network': network, 'room': room, 'from': sender, 'short': '',
                           'long': '', 'text': text, 'rx_time': int(time.time()), 'mine': True,
                           'id': packet_id, 'status': 'sending'})
        self.log(f'{NETWORK_LABELS[network]} {room}: message published; waiting for the broker to echo it.')
        draft.set('')
        return True

    def receive_mqtt_text(self, network, result):
        """Channel text opened with the operator's key goes to that channel's CHAT tab."""
        s = result.summary
        event = {'type': 'mqtt', 'network': network, 'room': s.channel_id, 'from': f'!{s.from_id:08x}',
                 'short': result.short, 'long': result.long, 'text': result.text,
                 'rx_time': int(time.time()), 'mine': False}
        if not self.chat_log.add(event):
            return
        self.add_chat_tab_if_new(event_key(event))
        self.note_chat_message(event)

    def add_chat_tab_if_new(self, key):
        """Redraw an open CHAT page so a new conversation gets its tab, keeping the reader's tab."""
        tabs = self.chat_tabs
        if key in self.chat_views or tabs is None or not tabs.winfo_exists():
            return
        showing = self.visible_chat_channel()
        if showing is None:
            return
        self.select_page('CHAT')
        frame = self.chat_tab_frames.get(showing)
        if frame is not None:
            self.chat_tabs.select(frame)

    def refresh_mqtt_states(self):
        """Per-network status words for the dashboard, Network page and status bar; set only on change."""
        states = getattr(self, 'mqtt_states', None)
        if not states:
            return                      # partially built app (tests, or before build)
        snapshot = self.dual_mqtt.snapshot() if self.dual_mqtt is not None else {}
        live = 0
        for network, variable in states.items():
            status = str((snapshot.get(network) or {}).get('status') or 'Off')
            friendly = {'Subscription requested (not yet confirmed)': 'Connected — subscribing',
                        'Subscribed — listening': 'Connected — listening',
                        'Disabled': 'Off', 'Stopped': 'Off'}.get(status, status)
            if friendly.startswith('Connected'):
                live += 1
            if variable.get() != friendly:
                variable.set(friendly)
        summary = f'MQTT: {live} of {len(self.mqtt_states)} networks live' if live else 'MQTT: off'
        if self.mqtt_summary.get() != summary:
            self.mqtt_summary.set(summary)

    def start_opted_in_mqtt(self):
        """Only explicitly enabled and auto-connect profiles may open broker sessions."""
        if self.demo.get():
            return
        config = self.read_config()
        selected = [name for name in ('centralnj', 'sjmesh')
                    if config.getboolean('mqtt_' + name, 'enabled', fallback=False)
                    and config.getboolean('mqtt_' + name, 'auto_connect', fallback=False)]
        if not selected:
            return
        from sitdown_dual_mqtt import DualMQTTMonitors
        if self.dual_mqtt is None:
            self.dual_mqtt = DualMQTTMonitors(config)
        for name in selected:
            self.dual_mqtt.connect(name)
            self.log(f'{name}: receive-only MQTT auto-connect requested.')

    def mqtt_network_status(self):
        """Show each receive-only broker connection and its MQTT RX count."""
        if self.dual_mqtt is None:
            lines = [f'{name}: Not connected; MQTT RX 0' for name in ('centralnj', 'sjmesh')]
        else:
            snapshots = self.dual_mqtt.snapshot()
            stats = self.dual_mqtt.ingest_stats()
            lines = [f"{name}: {snapshots[name]['status']}; MQTT RX {snapshots[name]['total']}\n"
                     f"  {format_stats(stats[name])}"
                     for name in ('centralnj', 'sjmesh')]
        self.show_info('Mesh Network Status', '\n'.join(lines))

    def mqtt_network_action(self, network, connect):
        """Manual receive-only connection for one saved network profile."""
        from sitdown_dual_mqtt import DualMQTTMonitors
        label = NETWORK_LABELS.get(network, network)
        if not connect:
            if self.dual_mqtt is not None:
                self.dual_mqtt.stop(network)
            self.log(f'{label}: disconnected.')
            self.refresh_mqtt_states()
            return
        if self.demo.get():
            self.show_info('Mesh Networks', 'Demo mode does not connect to brokers.')
            return
        if self.dual_mqtt is None:
            self.dual_mqtt = DualMQTTMonitors(self.read_config())
        ok = self.dual_mqtt.connect(network)
        status = self.dual_mqtt.snapshot()[network]['status']
        self.refresh_mqtt_states()
        if ok:
            self.log(f'{label}: connecting to the broker…')
        else:
            self.show_info(f'{label} not connected', f'{status}. Check the {label} tab in Settings.')

    def integration_settings(self, page):
        ui, c = self.ui, THEME
        config = self.read_config()
        tabs = ttk.Notebook(page)
        fields = {}
        self.setting_fields = fields
        sections = SETTINGS_SECTIONS

        general = tk.Frame(tabs, bg=c['surface'])
        tabs.add(general, text='General')
        body = self.scrollable(general)
        body.configure(padx=SPACE['xl'], pady=SPACE['md'])
        ui.section(body, 'Appearance')
        ui.label(body, 'Theme', 'body', bg='surface').pack(fill='x')
        ui.segmented(body, self.appearance, (('Dark', 'dark'), ('Light', 'light'))).pack(anchor='w', pady=(2, SPACE['md']))
        ui.label(body, 'Text size', 'body', bg='surface').pack(fill='x')
        ui.segmented(body, self.text_size, (('Small', 'small'), ('Default', 'default'), ('Large', 'large'),
                                            ('Larger', 'larger'))).pack(anchor='w', pady=(2, 0))
        ui.section(body, 'Sending')
        ui.toggle(body, 'Allow sending this session', self.centralnj_send_enabled,
                  'Chat, direct messages and announcements stay blocked until this is on. It turns off when you quit, '
                  'and each channel still asks you once.')
        ui.button(body, 'Check send safety', self.check_centralnj_send_safety, 'ghost', small=True).pack(anchor='w')
        ui.section(body, 'Backups')
        ui.label(body, 'A backup holds your settings, chat history, saved node list and BBS databases (mail, '
                 'boards). It may contain messages and keys, so keep it private. Restoring saves a safety copy of '
                 'your current files first.', 'small', 'muted', bg='surface', wrap=620).pack(fill='x')
        line = tk.Frame(body, bg=c['surface'])
        line.pack(fill='x', pady=(SPACE['sm'], 0))
        ui.button(line, 'Back up now…', self.backup, small=True).pack(side='left')
        ui.button(line, 'Verify a backup…', self.verify_backup, 'ghost', small=True).pack(side='left', padx=SPACE['xs'])
        ui.button(line, 'Restore from backup…', self.restore_backup, 'ghost', small=True).pack(side='left')
        ui.section(body, 'Troubleshooting')
        line = tk.Frame(body, bg=c['surface'])
        line.pack(fill='x')
        ui.button(line, 'Copy diagnostics', self.copy_diagnostics, small=True).pack(side='left')
        ui.button(line, 'Radio + MQTT setup guide', lambda: self.show_info('CentralNJ MQTT / Radio Setup', SETUP_GUIDE),
                  'ghost', small=True).pack(side='left', padx=SPACE['xs'])

        for section, (tab_name, entries) in sections.items():
            holder = tk.Frame(tabs, bg=c['surface'])
            tabs.add(holder, text=tab_name)
            frame = self.scrollable(holder)
            frame.configure(padx=SPACE['xl'], pady=SPACE['md'])
            if section in ('mqtt_centralnj', 'mqtt_sjmesh'):
                network = section[5:]
                head = tk.Frame(frame, bg=c['surface'])
                head.pack(fill='x', pady=(0, SPACE['sm']))
                ui.pill(head, self.mqtt_states[network], role='body').pack(side='left')
                ui.button(head, 'Disconnect', lambda n=network: self.mqtt_network_action(n, False), 'ghost',
                          small=True).pack(side='right')
                ui.button(head, 'Connect', lambda n=network: self.mqtt_network_action(n, True), small=True).pack(
                    side='right', padx=SPACE['xs'])
            if section == 'personality':
                self.greeting_settings(frame)
            grid = None
            row = 0
            for key, label, default, help_text in entries:
                if key == '#':
                    ui.section(frame, label)
                    if help_text:
                        ui.label(frame, help_text, 'small', 'muted', bg='surface', wrap=620).pack(fill='x')
                    grid = None
                    continue
                if default.startswith('bool'):
                    var = tk.BooleanVar(value=config.getboolean(section, key, fallback=default == 'bool_true'))
                    ui.toggle(frame, label, var, help_text)
                    grid = None
                else:
                    if grid is None:
                        grid = tk.Frame(frame, bg=c['surface'])
                        grid.pack(fill='x')
                        grid.grid_columnconfigure(1, weight=1)
                        row = 0
                    var = tk.StringVar(value=config.get(section, key, fallback=default))
                    ui.field(grid, label, var, help_text, secret=key in ('password', 'channel_keys'), row=row)
                    row += 1
                fields[(section, key)] = var
            if section == 'mqtt_monitor':
                ui.button(frame, 'Check this configuration', lambda: check_mqtt_configuration(), 'ghost',
                          small=True).pack(anchor='w', pady=(SPACE['md'], 0))

        def save():
            for (section, key), var in fields.items():
                if not config.has_section(section):
                    config.add_section(section)
                config.set(section, key, str(var.get()).lower() if isinstance(var, tk.BooleanVar) else var.get())
            try:
                Personality(public_mode=config.get('personality', 'public_mode'))
                channel = config.getint('startup', 'channel')
                if not 0 <= channel <= 7:
                    raise ValueError('Startup channel must be 0–7.')
                if len(config.get('startup', 'message').encode('utf-8')) > 200:
                    raise ValueError('Startup message exceeds 200 UTF-8 bytes.')
                if config.getboolean('weather', 'internet_enabled'):
                    if not (-90 <= config.getfloat('weather', 'latitude') <= 90 and -180 <= config.getfloat('weather', 'longitude') <= 180):
                        raise ValueError('Invalid weather coordinates.')
                if config.getboolean('mqtt_monitor', 'enabled'):
                    if not config.get('mqtt_monitor', 'host').strip() or not config.get('mqtt_monitor', 'topic').strip():
                        raise ValueError('MQTT requires an explicit broker and topic.')
                    if not 1 <= config.getint('mqtt_monitor', 'port') <= 65535:
                        raise ValueError('Invalid MQTT port.')
                for section in ('mqtt_centralnj', 'mqtt_sjmesh'):
                    if config.getboolean(section, 'enabled', fallback=False):
                        if not config.get(section, 'host', fallback='').strip():
                            raise ValueError(f'{section}: broker hostname is required.')
                        topic = config.get(section, 'topic', fallback='').strip()
                        from sitdown_dual_mqtt import topics_allowed
                        if not topics_allowed(topic):
                            raise ValueError(f'{section}: use 1–4 different comma-separated topics, each exact or '
                                             'msh/<region>/2/e/<channel>/+ for one channel. No other wildcards.')
                        if not 1 <= config.getint(section, 'port') <= 65535:
                            raise ValueError(f'{section}: port must be 1–65535.')
                    send_as = config.get(section, 'send_as', fallback='').strip()
                    if send_as and peer_id(send_as) is None:
                        raise ValueError(f'{section}: "Send as node id" must look like !1234abcd.')
                    from sitdown_mqtt_crypto import parse_channel_keys
                    try:
                        parse_channel_keys(config.get(section, 'channel_keys', fallback=''))
                    except ValueError as exc:
                        raise ValueError(f'{section} channel keys: {exc}') from None
                path = self.config_path()
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix('.ini.tmp')
                with temporary.open('w') as stream:
                    config.write(stream)
                temporary.chmod(0o600)
                temporary.replace(path)
                self.settings_saved(config)
            except (ValueError, OSError) as exc:
                self.show_info('Settings not saved', str(exc))

        def check_mqtt_configuration():
            """Validate current unsaved form values offline, without opening a connection."""
            preview = configparser.ConfigParser()
            preview.add_section('mqtt_monitor')
            for key, _label, _default, _help in sections['mqtt_monitor'][1]:
                if key == '#':
                    continue
                var = fields[('mqtt_monitor', key)]
                preview.set('mqtt_monitor', key, str(var.get()).lower() if isinstance(var, tk.BooleanVar) else var.get())
            self.show_info('MQTT Configuration Check', ActivityCounter(preview).diagnostics())

        actions = tk.Frame(page, bg=c['bg'])
        actions.pack(side='bottom', fill='x', pady=(SPACE['sm'], SPACE['sm']))
        ui.button(actions, 'Save changes', save, 'primary', bg='bg').pack(side='right')
        ui.label(actions, 'Server settings apply the next time the BBS server starts. Appearance and sending apply '
                 'right away.', 'small', 'muted').pack(side='left')
        # Pack the expandable notebook last so the save bar stays visible on small screens.
        tabs.pack(side='top', fill='both', expand=True)

    def scrollable(self, parent):
        """Return a frame inside parent that scrolls vertically only when it does not fit."""
        bg = THEME['surface']
        canvas = tk.Canvas(parent, bg=bg, highlightthickness=0, bd=0)
        bar = ttk.Scrollbar(parent, orient='vertical', command=canvas.yview)
        inner = tk.Frame(canvas, bg=bg)
        window = canvas.create_window(0, 0, window=inner, anchor='nw')
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side='left', fill='both', expand=True)

        def refresh(_event=None):
            canvas.itemconfigure(window, width=canvas.winfo_width())
            canvas.configure(scrollregion=(0, 0, inner.winfo_reqwidth(), inner.winfo_reqheight()))
            if inner.winfo_reqheight() > canvas.winfo_height() > 1:
                bar.pack(side='right', fill='y', before=canvas)
            else:
                bar.pack_forget()
                canvas.yview_moveto(0)
        inner.bind('<Configure>', refresh)
        canvas.bind('<Configure>', refresh)

        def wheel(event):
            if bar.winfo_ismapped():
                canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units')
        canvas.bind('<Enter>', lambda _e: canvas.bind_all('<MouseWheel>', wheel))
        canvas.bind('<Leave>', lambda _e: canvas.unbind_all('<MouseWheel>'))
        return inner

    # ------------------------------------------------------------------ feedback

    def check_centralnj_send_safety(self):
        """Offline preflight only; never sends a packet or publishes MQTT."""
        allowed, reason = send_preflight(enabled=self.centralnj_send_enabled.get(),
                                          confirmed=False, radio_ready=False,
                                          demo_mode=self.demo.get(), message='')
        self.show_info('CentralNJ Send Safety', reason)
        return allowed

    def show_info(self, title, message):
        """Non-blocking themed message; the accent shows success, warning or error alongside the words."""
        c, ui = THEME, self.ui
        lowered = title.lower()
        kind = ('error' if any(w in lowered for w in ('fail', 'not ', 'error', 'need', 'missing', "couldn't")) else
                'success' if any(w in lowered for w in ('saved', 'verified', 'copied')) else
                'warning' if any(w in lowered for w in ('check', 'demo', 'safety', 'wait')) else 'info')
        accent = {'error': 'danger', 'success': 'success', 'warning': 'warning', 'info': 'info'}[kind]
        icon = {'error': '■', 'success': '✓', 'warning': '▲', 'info': 'ⓘ'}[kind]
        previous_focus = self.root.focus_get()
        win = tk.Toplevel(self.root)
        win.title(title)
        win.transient(self.root)
        win.configure(bg=c['background'])
        win.resizable(False, False)
        tk.Frame(win, bg=c[accent], height=3).pack(fill='x')
        head = tk.Frame(win, bg=c['background'])
        head.pack(fill='x', padx=SPACE['xl'], pady=(SPACE['lg'], SPACE['sm']))
        tk.Label(head, text=icon, font=ui.font('title'), fg=c[accent], bg=c['background']).pack(side='left',
                                                                                             padx=(0, SPACE['sm']))
        tk.Label(head, text=title, font=ui.font('title'), fg=c['text'], bg=c['background'], wraplength=420,
                 justify='left', anchor='w').pack(side='left', fill='x')
        tk.Label(win, text=message, font=ui.font('body'), fg=c['muted'], bg=c['background'], wraplength=460,
                 justify='left', anchor='w').pack(fill='x', padx=SPACE['xl'], pady=(0, SPACE['lg']))

        def dismiss(_event=None):
            win.destroy()
            if previous_focus is not None and previous_focus.winfo_exists():
                previous_focus.focus_set()
            return 'break'
        close = ui.button(win, 'OK', dismiss, 'primary', bg='bg')
        close.pack(anchor='e', padx=SPACE['xl'], pady=(0, SPACE['lg']))
        win.bind('<Escape>', dismiss)
        win.bind('<Return>', dismiss)
        win.protocol('WM_DELETE_WINDOW', dismiss)
        win.update_idletasks()
        x = max(0, self.root.winfo_rootx() + (self.root.winfo_width() - win.winfo_reqwidth()) // 2)
        y = max(0, self.root.winfo_rooty() + (self.root.winfo_height() - win.winfo_reqheight()) // 3)
        win.geometry(f'+{x}+{y}')
        close.focus_set()
        return win

    def refresh_connection_summary(self, *_args):
        """Display configured endpoint without claiming it is connected."""
        kind = "TCP / Network" if self.connection.get() == "tcp" else "USB / Serial"
        endpoint = self.address.get().strip() or "Not specified (auto/default)"
        self.connection_summary.set(f"Configured: {kind}\nEndpoint: {endpoint}")
        label = getattr(self, 'address_label', None)
        if label is not None:
            label.set('Radio IP address or hostname' if self.connection.get() == 'tcp'
                      else 'USB port (leave blank to pick automatically)')

    def refresh_status_indicator(self, *_args):
        """The header button offers the next action; the pill beside it shows process state, not radio link."""
        button = getattr(self, 'server_button', None)
        if button is None or not button.winfo_exists():
            return
        running = bool(self.supervisor and self.supervisor.running) or str(self.status.get()).startswith('Demo running')
        button.configure(text='Stop server' if running else 'Start server')

    def show_home(self):
        self.select_page('HOME')

    def show_about(self):
        self.select_page('ABOUT')

    def styled_button(self, parent, text, command, color=None, compact=False):
        """Compatibility wrapper for older call sites: maps a colour to a design-system button kind."""
        kind = 'primary' if color in (None, THEME['primary'], THEME['connected']) else 'secondary'
        return self.ui.button(parent, text, command, kind, small=compact, bg=self.surface_key(parent))

    def log(self, message):
        self.output.configure(state="normal")
        tag = ("event_error" if any(word in message.lower() for word in
                ("error", "failed", "traceback")) else
               "event_ready" if any(word in message.lower() for word in
                ("initialized", "ready", "running")) else None)
        display = message.replace("\\n", "\n    ") if "Sending message to user" in message else message
        self.output.insert("end", display + "\n", (tag,) if tag else ())
        if int(self.output.index("end-1c").split(".")[0]) > 2000:
            self.output.delete("1.0", "300.0")
        self.output.see("end")
        self.output.configure(state="disabled")
        self._sync_expanded_log()

    def expand_log(self):
        """Open a large, resizable live view without disturbing the server."""
        window = getattr(self, '_expanded_log_window', None)
        if window is not None and window.winfo_exists():
            window.lift()
            window.focus_force()
            return
        c = THEME
        window = tk.Toplevel(self.root)
        self._expanded_log_window = window
        window.title('The Sitdown BBS — Activity log')
        window.geometry('1000x650')
        window.minsize(550, 300)
        frame = tk.Frame(window, bg=c['bg'], padx=SPACE['md'], pady=SPACE['md'])
        frame.pack(fill='both', expand=True)
        scrollbar = ttk.Scrollbar(frame)
        scrollbar.pack(side='right', fill='y')
        viewer = tk.Text(frame, bg=c['log_bg'], fg=c['log_text'], font=self.ui.font('mono'), wrap='word',
                         padx=SPACE['lg'], pady=SPACE['md'], state='disabled', relief='flat',
                         highlightthickness=1, highlightbackground=c['border'], yscrollcommand=scrollbar.set)
        viewer.pack(side='left', fill='both', expand=True)
        scrollbar.configure(command=viewer.yview)
        self._expanded_log_view = viewer
        self._sync_expanded_log()

    def _sync_expanded_log(self):
        """Mirror bounded log text; never change the source or radio session."""
        window = getattr(self, '_expanded_log_window', None)
        if window is None or not window.winfo_exists():
            return
        viewer = self._expanded_log_view
        viewer.configure(state="normal")
        viewer.delete("1.0", "end")
        viewer.insert("1.0", self.output.get("1.0", "end-1c"))
        viewer.see("end")
        viewer.configure(state="disabled")

    def find_log(self):
        """Find the next match without altering or filtering server output."""
        query = self.log_query.get().strip()
        if not query:
            return
        self.output.tag_remove("search_hit", "1.0", "end")
        start = self.output.index("insert + 1c")
        match = self.output.search(query, start, stopindex="end", nocase=True)
        if not match:
            match = self.output.search(query, "1.0", stopindex="end", nocase=True)
        if match:
            end = self.output.index(f"{match} + {len(query)}c")
            self.output.tag_add("search_hit", match, end)
            self.output.tag_configure("search_hit", background=THEME["highlight"], foreground=THEME["background"])
            self.output.mark_set("insert", match)
            self.output.see(match)
        else:
            self.show_info("The Backroom", "No matching log entry found.")

    def show_nodes(self):
        self.select_page('NODES')

    def clear_log(self):
        """Clear the visible log only; the running server is unaffected."""
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.configure(state="disabled")
        self._sync_expanded_log()
        self.log("The Backroom: visible log cleared.")

    def export_log(self):
        """Save a snapshot of the visible log without stopping the server."""
        destination = filedialog.asksaveasfilename(
            parent=self.root, title="Save The Backroom log",
            defaultextension=".txt", initialfile="sitdown-bbs-log.txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if not destination:
            return
        try:
            snapshot = self.output.get("1.0", "end-1c")
            Path(destination).write_text(snapshot + "\n", encoding="utf-8")
        except OSError as exc:
            self.show_info("Couldn't save the log", str(exc))
            return
        self.log("The Backroom: log exported.")

    def list_ports(self):
        try:
            from serial.tools import list_ports
            ports = [p.device for p in list_ports.comports()]
            self.log("USB ports: " + (", ".join(ports) if ports else "None detected"))
            if len(ports) == 1 and self.connection.get() == "serial":
                self.address.set(ports[0])
        except ImportError:
            self.log("pyserial is missing. Install requirements.txt.")

    def check_radio_connection(self):
        """Snapshot input on Tk's thread; perform only transport checks in a worker."""
        if self.preflight_pending:
            self.show_info('Radio Connection Preflight', 'A check is already running.')
            return
        connection, address = self.connection.get(), self.address.get()
        demo = self.demo.get()
        busy = bool((self.supervisor and self.supervisor.running) or self.restart_at is not None)
        self.preflight_pending = True
        self.log('Radio preflight started; no messages or settings will be sent.')

        def worker():
            try:
                report = check_radio(connection, address, demo=demo, busy=busy)
            except Exception:
                report = 'Radio preflight could not complete. Meshtastic protocol: Unverified.'
            self.preflight_results.put(report)

        threading.Thread(target=worker, daemon=True).start()

    def start(self):
        if getattr(self, 'preflight_pending', False):
            self.show_info('Radio Connection Preflight', 'Wait for the current check before starting the BBS.')
            return
        if self.supervisor and self.supervisor.running:
            return
        self.restart_at = None
        if self.demo.get():
            self.snapshot = {'type': 'snapshot', 'channels': [{'index': 0, 'name': 'DEMO Local'}],
                'nodes': [{'id': '!abcdef01', 'shortName': 'DEMO', 'longName': 'Demonstration node',
                          'hardware': 'Demo', 'role': 'CLIENT', 'lastHeard': None, 'snr': None,
                          'hopsAway': None, 'deviceMetrics': {}, 'position': {}}],
                'mqtt': {'status': 'Disabled (demo)', 'total': 0, 'topics': []}}
            self.status.set("Demo running — no radio")
            self.radio_activity.set("Radio activity: Demo only")
            self.log("DEMO: No radio messages are sent.")
            self.retry_count = 0
            return
        DATA.mkdir(parents=True, exist_ok=True)
        config = DATA / "config.ini" if getattr(sys, "frozen", False) else ROOT / "config.ini"
        if not config.exists() and getattr(sys, "frozen", False) and (ROOT / "example_config.ini").exists():
            shutil.copy2(ROOT / "example_config.ini", config)
            self.log("Created config.ini in ~/.sitdown-bbs. Review it before connecting a radio.")
        if not config.exists():
            self.show_info("Server not started — settings file missing", "There is no config.ini yet. Copy example_config.ini to config.ini next to the app and review it, then press Start again.")
            return
        addr = self.address.get().strip()
        if self.connection.get() == "tcp" and not addr:
            self.show_info("Server not started — radio address needed", "Network connections need the radio's IP address or hostname. Enter it under Radio connection on the Dashboard.")
            return
        self.snapshot = {'nodes': [], 'channels': [], 'mqtt': {'status': 'Awaiting server', 'total': 0, 'topics': []}}
        self.save()
        cmd = ([sys.executable, "--sitdown-server"] if getattr(sys, "frozen", False)
               else [sys.executable, "-u", str(ROOT / "server.py")]) + ["--config", str(config), "--desktop-control",
               "--interface-type", self.connection.get()]
        if addr:
            cmd += ["--host" if self.connection.get() == "tcp" else "--port", addr]
        try:
            self.supervisor = ServerSupervisor(cmd, DATA if getattr(sys, "frozen", False) else ROOT)
            self.supervisor.start()
            self.status.set("Starting...")
            self.link_warning.set("Radio: connecting…")
            if hasattr(self, "radio_activity"):
                self.radio_activity.set("Radio activity: Awaiting messages")
            self.log("Starting The Sitdown BBS...")
        except OSError as exc:
            self.supervisor = None
            self.status.set("Start failed")
            self.log(f"Start failed: {exc}")
            self.schedule_recovery()

    def poll(self):
        try:
            report = self.preflight_results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.preflight_pending = False
            self.log(report)
            self.show_info('Radio Connection Preflight', report)
        monitor = self.supervisor
        if monitor:
            for kind, value in monitor.poll_events():
                if kind == "log":
                    self.log(value)
                elif kind == "state":
                    self.status.set(value)
                elif kind == "activity" and value == "rx":
                    self.radio_activity.set("Radio RX observed in BBS logs")
                elif kind == "link_state":
                    self.link_warning.set("Radio disconnected — waiting 30s for it to come back" if value == "disconnected" else "Radio connected")
                elif kind == "control":
                    if value.get('type') == 'snapshot':
                        self.snapshot = value
                        if value.get('me'):
                            self.radio_node = value['me']      # kept for MQTT sends after the server stops
                        self.record_radio_nodes(value.get('nodes'))
                    elif value.get('type') == 'chat':
                        if self.chat_log.add(value):
                            self.note_chat_message(value)
                    elif value.get('type') == 'dm':
                        self.receive_dm(value)
                    elif value.get('type') == 'chat_status':
                        sent = self.chat_log.set_status(value.get('id'), value.get('status'), value.get('reason'))
                        if sent is not None:
                            self.log_chat_status(sent)
                    elif value.get('action') in ('dm', 'dm_end'):
                        self.dm_result(value)
                    elif value.get('action') == 'chat':
                        if value.get('error'):
                            self.log('Chat not sent: ' + value['error'])
                            self.show_info('Chat not sent', value['error'])
                        else:
                            self.chat_log.add(value.get('chat') or {})
                            self.log(value.get('text', 'Chat message submitted.'))
                    else:
                        self.log(value.get('error') or value.get('text', 'Operation complete.'))
                        self.show_info('The Sitdown', value.get('error') or value.get('text', 'Operation complete.'))
                elif kind == "link_error":
                    self.link_warning.set(explain_transport_error(value, self.connection.get()))
            if hasattr(self, "traffic"):
                self.traffic.set(f"Messages observed: RX {monitor.rx_count} / TX {monitor.tx_count}")
            if monitor.running:
                if monitor.link_state == "disconnected":
                    self.link_warning.set("Radio disconnected — waiting 30s for it to come back")
                if monitor.last_rx_at is not None:
                    age = max(0, int(time.time() - monitor.last_rx_at))
                    self.radio_activity.set(f"Radio RX observed {age}s ago")
                self.uptime.set(f"Uptime: {monitor.uptime_seconds()}s")
            elif not monitor.stopping and self.restart_at is None:
                code = monitor.process.poll() if monitor.process else "unknown"
                if code == 2:
                    self.log("BBS server stopped: it couldn't reach the radio (see the line above).")
                    self.status.set("Can't reach radio")
                else:
                    self.log(f"BBS process exited ({code}).")
                    self.status.set("Disconnected / process stopped")
                self.link_warning.set("Radio: not connected (server stopped)")
                self.schedule_recovery()
                self.supervisor = None
                self.radio_activity.set("Radio activity: Process stopped")
        self.drain_mqtt_events()
        self.refresh_mqtt_states()
        self.refresh_chat_views()
        # Only SDK link events count as connected; MQTT monitor is read-only.
        radio_state = 'unverified'
        if monitor and monitor.running:
            if monitor.link_state == 'sdk_connected':
                radio_state = 'connected'
            elif monitor.link_state == 'disconnected':
                radio_state = 'error'
        mqtt_status = str(self.snapshot.get('mqtt', {}).get('status', 'Disabled')).lower()
        mqtt_state = ('connected' if mqtt_status.startswith('connected') else
                      'error' if any(word in mqtt_status for word in
                                     ('unavailable', 'failed', 'refused', 'disconnected')) else
                      'disabled' if 'disabled' in mqtt_status else 'unverified')
        self.update_centralnj_status(radio_state, mqtt_state)
        if self.restart_at is not None and (not self.auto_restart.get() or self.demo.get()):
            self.restart_at = None
            self.status.set("Stopped — recovery cancelled")
        if self.restart_at is not None and self.restart_at > time.monotonic():
            remaining = max(1, int(self.restart_at - time.monotonic() + 0.999))
            self.status.set(f"Recovery in {remaining}s (attempt {self.retry_count})")
        if self.restart_at is not None and time.monotonic() >= self.restart_at:
            self.restart_at = None
            self.start()
        if self.root.winfo_exists():
            self.root.after(500, self.poll)

    def schedule_recovery(self):
        """Opt-in capped backoff; no retries when recovery is disabled."""
        if self.auto_restart.get() and not self.demo.get():
            self.retry_count += 1
            delay = min(60, 2 ** min(self.retry_count, 6))
            self.restart_at = time.monotonic() + delay
            self.log(f"Retrying process in {delay}s (attempt {self.retry_count}).")
            self.status.set(f"Retry in {delay}s (attempt {self.retry_count})")

    def settings_saved(self, config):
        """Report a save, flag keys that match no watched channel, and offer to apply everything now."""
        warnings = []
        for network, label in NETWORK_LABELS.items():
            section = 'mqtt_' + network
            stray = unmatched_key_names(config.get(section, 'channel_keys', fallback=''),
                                        config.get(section, 'topic', fallback=''))
            if stray:
                warnings.append(f"{label}: the key for {', '.join(stray)} matches none of your topics, so it "
                                f"won't be used. Use the channel name from a topic like msh/US/2/e/<name>/+.")
        if self.dual_mqtt is not None:
            from sitdown_dual_mqtt import channel_keys
            self.dual_mqtt.pipeline.keys = channel_keys(config)          # new keys read text right away
        server_running = bool(self.supervisor and self.supervisor.running)
        mqtt_live = [n for n, m in (self.dual_mqtt.monitors.items() if self.dual_mqtt is not None else ())
                     if m.client is not None]
        message = 'Your settings are saved.'
        if warnings:
            message += '\n\n' + '\n'.join(warnings)
        if not (server_running or mqtt_live) or self.demo.get():
            self.show_info('Settings saved', message + '\n\nThey apply the next time the server starts.')
            return
        parts = (['restarts the BBS server'] if server_running else []) + (
            ['reconnects ' + ' and '.join(NETWORK_LABELS[n] for n in mqtt_live)] if mqtt_live else [])
        if messagebox.askyesno('Apply settings now?', message + f"\n\nApply now? This {' and '.join(parts)}.",
                               parent=self.root):
            self.apply_saved_settings(config, server_running, mqtt_live)
        elif warnings:
            self.show_info('Check your channel keys', '\n'.join(warnings))

    def apply_saved_settings(self, config, server_running, mqtt_live):
        """Restart the server and rebuild MQTT connections with the saved settings."""
        if mqtt_live:
            from sitdown_dual_mqtt import DualMQTTMonitors
            self.dual_mqtt.stop()
            self.dual_mqtt = DualMQTTMonitors(config)
            for network in mqtt_live:
                self.dual_mqtt.connect(network)
            self.log('Reconnected ' + ', '.join(NETWORK_LABELS[n] for n in mqtt_live) + ' with the new settings.')
        if server_running:
            self.restart()
        self.refresh_mqtt_states()

    def restart(self):
        """Restart on demand without scheduling crash recovery."""
        self.stop()
        self.log("Manual server restart requested.")
        self.start()

    def copy_diagnostics(self):
        """Copy useful local state; do not assert radio connectivity."""
        monitor = self.supervisor
        details = [
            f"The Sitdown BBS {VERSION} — local diagnostics",
            f"Process status: {self.status.get()}",
            f"Mode: {'Demo (no radio)' if self.demo.get() else self.connection.get().upper()}",
            f"Endpoint: {self.address.get().strip() or '(default)'}",
            f"Server process running: {bool(monitor and monitor.running)}",
            f"Known nodes (radio database): {len(self.snapshot.get('nodes') or [])}",
            f"Uptime: {monitor.uptime_seconds() if monitor else 0}s",
            f"Radio activity: {self.radio_activity.get()}",
            f"Observed RX/TX: {monitor.rx_count if monitor else 0}/{monitor.tx_count if monitor else 0}",
            f"Last transport error: {monitor.last_link_error if monitor else None}",
            f"SDK session: {monitor.link_state if monitor else 'unverified'}",
            f"Recovery enabled: {self.auto_restart.get()}",
            f"Recovery attempts: {self.retry_count}",
            "Radio connection verified: Log-observed activity only, not continuous link health",
        ]
        if self.dual_mqtt is not None:
            snapshots, stats = self.dual_mqtt.snapshot(), self.dual_mqtt.ingest_stats()
            details += [f"MQTT {NETWORK_LABELS[name]}: {snapshots[name]['status']}; {format_stats(stats[name])}"
                        for name in NETWORK_LABELS]
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(details))
        self.root.update()
        self.log("Local diagnostics copied to clipboard.")

    def stop(self):
        self.restart_at = None
        self.retry_count = 0
        if self.supervisor:
            self.supervisor.stop()
            self.supervisor = None
        self.status.set("Stopped")
        self.link_warning.set("Radio: not connected (server stopped)")
        if hasattr(self, "radio_activity"):
            self.radio_activity.set("Radio activity: Process stopped")
        self.uptime.set("Uptime: 0s")
        self.log("Fuggedaboutit. Server stopped.")

    def commands(self):
        """The radio command list now lives on the Broadcast page."""
        self.select_page('BBS')
        tabs = getattr(self, 'broadcast_tabs', None)
        if tabs is not None and tabs.winfo_exists():
            tabs.select(2)

    def settings(self):
        self.select_page('SETTINGS')

    def backup(self):
        DATA.mkdir(parents=True, exist_ok=True)
        destination = filedialog.asksaveasfilename(defaultextension=".zip",
                     initialfile="sitdown-backup.zip", filetypes=[("Zip archive", "*.zip")])
        if not destination:
            return
        try:
            database_paths = set(DATA.glob("*.db")) | set(self.config_path().parent.glob("*.db"))
            create_backup_archive(destination, [CONFIG, self.config_path(), CONFIG.with_name('chat-history.json')],
                                  sorted(database_paths))
        except (OSError, ValueError, sqlite3.DatabaseError, zipfile.BadZipFile) as exc:
            self.log(f"The Books: backup failed: {exc}")
            self.show_info("Backup failed", f"No new backup was saved. {exc}")
            return
        self.log("The Books: verified backup saved. Treat backups as sensitive; they may contain messages and keys.")

    def restore_targets(self):
        """Where each restorable file lives on this Mac."""
        targets = {'launcher.json': CONFIG, 'config.ini': self.config_path(),
                   'chat-history.json': CONFIG.with_name('chat-history.json'),
                   'node-directory.db': CONFIG.with_name('node-directory.db'),
                   'bulletins.db': self.config_path().parent / 'bulletins.db',
                   'sitdown-broadcasts.db': self.config_path().parent / 'sitdown-broadcasts.db'}
        return {name: Path(path) for name, path in targets.items() if name in RESTORABLE}

    def restore_backup(self):
        """Replace the app's files with a verified backup, after saving a safety copy of the current ones."""
        if self.supervisor and self.supervisor.running:
            if not messagebox.askyesno('Stop the server to restore?', 'Restoring replaces the BBS databases, so '
                                       'the server has to stop first. Stop it now?', parent=self.root):
                return
            self.stop()
        selected = filedialog.askopenfilename(title='Restore from a backup', filetypes=[('Zip archive', '*.zip')])
        if not selected:
            return
        targets = self.restore_targets()
        try:
            restore, skipped = restore_plan(selected, targets)
        except (OSError, ValueError, sqlite3.DatabaseError, zipfile.BadZipFile, RuntimeError, EOFError) as exc:
            self.log(f'Restore not started: the backup did not pass checks ({exc}).')
            self.show_info('Backup not restored', 'That backup did not pass its checks, so nothing was changed. '
                           'Details are in the activity log.')
            return
        listing = '\n'.join(f'• {name}' for name in restore)
        if not messagebox.askyesno('Restore this backup?', f'These files on this Mac will be replaced:\n{listing}\n\n'
                                   'A safety copy of the current files is saved first, next to your settings.',
                                   parent=self.root):
            return
        directory = getattr(self, 'node_directory', None)
        if directory is not None:
            directory.close()                       # release the node list file before swapping it
        try:
            result = restore_backup_archive(selected, targets, DATA / 'restore-safety-copies')
        except (OSError, ValueError, sqlite3.DatabaseError, zipfile.BadZipFile) as exc:
            self.node_directory = self.open_node_directory()
            self.log(f'Restore failed: {exc}')
            self.show_info('Restore failed', f'{exc}. Files already replaced keep the backup version; the safety '
                           'copy has the previous ones.')
            return
        self.node_directory = self.open_node_directory()
        self.chat_log = ChatLog(path=CONFIG.with_name('chat-history.json'))
        self.load()
        self.log(f"Restored {len(result['restored'])} files from {Path(selected).name}."
                 + (f" Skipped: {', '.join(result['skipped'])}." if result['skipped'] else ''))
        safety = f"\n\nYour previous files are in {result['safety']}." if result['safety'] else ''
        self.show_info('Backup restored', f"Restored {len(result['restored'])} files.{safety}\n\n"
                       'Start the server to use the restored settings and databases.')
        self.select_page(self.current_page)

    def verify_backup(self):
        """Read-only validation of a selected archive; never restores live files."""
        selected = filedialog.askopenfilename(
            title="Verify The Books backup",
            filetypes=[("Zip archive", "*.zip")])
        if not selected:
            return
        try:
            report = inspect_backup_archive(selected)
        except (OSError, ValueError, sqlite3.DatabaseError, zipfile.BadZipFile,
                RuntimeError, EOFError) as exc:
            self.log(f"The Books: backup verification failed: {exc}")
            self.show_info("Backup verification failed",
                           "The selected backup did not pass validation. "
                           "No live files were changed. Check the server log for details.")
            return
        self.log(f"The Books: backup verified ({len(report['files'])} files, "
                 f"{len(report['databases'])} SQLite databases).")
        self.show_info("Backup verified",
                       f"Archive passed verification. Files: {len(report['files'])}. "
                       f"SQLite databases: {len(report['databases'])}. "
                       "No live files were changed. This is not a restore.")

    def close(self):
        if self.dual_mqtt is not None:
            self.dual_mqtt.stop()
        self.stop()
        self.root.destroy()


if __name__ == "__main__":
    if '--sitdown-smoke-test' in sys.argv:
        # Exercise the packaged imports and actual Tk pages without user config/radio.
        import tempfile
        import server
        import paho.mqtt.client
        from sitdown_mqtt_crypto import DEFAULT_KEY, crypt
        assert crypt(DEFAULT_KEY, 1, 2, crypt(DEFAULT_KEY, 1, 2, b'smoke')) == b'smoke'
        with tempfile.TemporaryDirectory() as folder:
            DATA = Path(folder)
            CONFIG = DATA / 'launcher.json'
            root = tk.Tk()
            app = SitdownApp(root)
            app.demo.set(True)
            app.start()
            for theme in ('light', 'dark'):
                app.appearance.set(theme)
                for page in ('BBS', 'MESSAGES', 'BOARDS', 'NODES', 'CHAT', 'WIRE', 'RADIO', 'ABOUT', 'SETTINGS', 'HOME'):
                    app.select_page(page)
                    root.update()
                    if page == 'HOME':
                        assert app.home_page.winfo_ismapped(), 'Home dashboard did not reappear'
                        assert app.active_page is None, 'Other page still active on Home'
                        assert app.home_page.winfo_children(), 'Home dashboard controls disappeared'
            app.text_size.set('larger')
            root.update()
            assert app.status.get() == 'Demo running — no radio'
            root.destroy()
        print(f'SITDOWN_SMOKE_OK version={VERSION}', flush=True)
        sys.exit(0)
    if "--sitdown-server" in sys.argv:
        sys.argv.remove("--sitdown-server")
        import server  # Ensures PyInstaller includes the server and its dependencies.
        runpy.run_module("server", run_name="__main__")
        sys.exit(0)
    root = tk.Tk()
    SitdownApp(root)
    root.mainloop()
