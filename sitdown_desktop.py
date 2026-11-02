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
from sitdown_utilities import format_node
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
from sitdown_errors import explain_transport_error
from sitdown_monitor import ServerSupervisor
from sitdown_mqtt_feed import MeshFeed, NETWORK_LABELS, format_stats
from sitdown_chat import ChatLog, status_label
from sitdown_version import VERSION
from sitdown_personality import Personality
import zipfile

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
DATA = Path.home() / ".sitdown-bbs"
CONFIG = DATA / "launcher.json"
THEME = {"background": "#030a0c", "panel": "#061013", "primary": "#ed001b",
         "connected": "#19e333", "text": "#F5F5F0", "highlight": "#d3dde0"}
LOG_MIN_LINES = 8
COMMANDS = [
    ("/sitdown", "Main menu"), ("/help", "Quick help"),
    ("/mail", "Check your mail"), ("/boards", "The Social Club"),
    ("/fortune", "Jersey wisdom"), ("/tools", "Utilities"),
    ("/cancel", "Return to main menu"),
    ("/node <ID or name>", "Who’s Who"), ("/weather", "Jersey Weather Desk"),
    ("/wire", "MQTT activity"), ("/announce <channel> <text>", "Admin broadcast"),
]


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
        self.chat_views = {}
        self.chat_confirmed = set()
        self.chat_unread = Counter()
        self.chat_tabs = None
        self.chat_tab_frames = {}
        self.supervisor = None
        self.restart_at = None
        self.retry_count = 0
        self.auto_restart = tk.BooleanVar(value=False)
        self.node_count = tk.StringVar(value='Known nodes: 0')
        self.uptime = tk.StringVar(value='Uptime: 0s')
        self.radio_activity = tk.StringVar(value='Radio activity: Not observed')
        self.traffic = tk.StringVar(value='Messages observed: RX 0 / TX 0')
        self.link_warning = tk.StringVar(value='Radio link: Unverified')
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
        self.colors = THEME
        self.root.title("THE SITDOWN BBS | North Jersey Communications Division")
        # Open taller when the screen allows it, so the server log is readable.
        height = max(940, min(1100, self.root.winfo_screenheight() - 80))
        self.root.geometry(f"1160x{height}")
        self.root.minsize(960, 760)
        self.root.configure(bg=THEME["background"])
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('TNotebook', background=THEME['background'], borderwidth=0)
        style.configure('TNotebook.Tab', background=THEME['panel'], foreground=THEME['text'], padding=(15, 10))
        style.map('TNotebook.Tab', background=[('selected', THEME['primary'])], foreground=[('selected', 'white')])
        style.configure('Desk.TEntry', fieldbackground='#0B141A', foreground='white', insertcolor='white', padding=8)
        style.configure('TCombobox', fieldbackground=THEME['background'], foreground=THEME['text'])
        style.map('TCombobox', fieldbackground=[('readonly', THEME['background'])], foreground=[('readonly', THEME['text'])])
        self.root.option_add('*Entry.highlightThickness', 1)
        self.root.option_add('*Entry.highlightBackground', '#527079')
        self.root.option_add('*Entry.relief', 'flat')
        self.load()
        self.build()
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
            except (ValueError, OSError):
                pass

    def save(self):
        try:
            Personality(self.desktop_mouth.get(), self.public_mouth.get(), self.public_profanity.get())
        except ValueError as exc:
            messagebox.showerror("Invalid Jersey Mouth setting", str(exc))
            return
        DATA.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps({
            "auto_start": self.auto_start.get(), "connection": self.connection.get(), "address": self.address.get(),
            "community": self.community.get(), "welcome": self.welcome.get(),
            "desktop_mouth": self.desktop_mouth.get(),
            "public_mouth": self.public_mouth.get(),
            "public_profanity": self.public_profanity.get()
        }, indent=2), encoding="utf-8")
        self.log("The Family Business: settings saved.")
        self.log(Personality(self.desktop_mouth.get(), self.public_mouth.get(), self.public_profanity.get()).message("welcome"))

    def build(self):
        c = self.colors
        def label(parent, text, size=11, color=None):
            return tk.Label(parent, text=text, font=("Helvetica", size, "bold"),
                            fg=color or c["text"], bg=c["background"])
        button = self.styled_button
        from PIL import Image
        with Image.open(ROOT / 'assets' / 'jersey-banner.png') as banner_file:
            self.banner_source = banner_file.convert('RGB')
        self._banner_size = None
        header = tk.Frame(self.root, bg=c['background'])
        header.pack(fill='x', padx=12, pady=(8, 0))
        self.banner = tk.Canvas(header, height=220, bg=c['background'], highlightthickness=0)
        self.banner.pack(fill='x')
        self.banner.bind('<Configure>', self.render_banner)
        navigation = tk.Frame(self.root, bg=c["panel"], pady=7)
        navigation.pack(fill="x", padx=12, pady=(2, 6))
        self.navigation = navigation
        self.nav_buttons = []
        self._nav_columns = None
        for title, action in [
            ("⌂  HOME", self.show_home),
            ("▣  BBS", self.commands),
            ("✆  CHAT", lambda: self.select_page("CHAT")),
            ("✉  MESSAGES", lambda: self.show_command_info("/mail")),
            ("▤  BOARDS", lambda: self.show_command_info("/boards")),
            ("◉  RADIO", self.show_radio),
            ("◎  NODES", self.show_nodes),
            ("⚙  SETTINGS", self.settings),
            ("ⓘ  ABOUT", self.show_about),
        ]:
            action = lambda name=title.split()[-1]: self.select_page(name)
            control = self.styled_button(
                navigation, title, action,
                c["primary"] if "HOME" in title else c["panel"],
                compact=True)
            # Labels can gain an unread count, so pages are matched by name, not label text.
            control.page_name, control.base_text = title.split()[-1], title
            self.nav_buttons.append(control)
        navigation.bind("<Configure>", self.layout_navigation)
        self.page_host = tk.Frame(self.root, bg=c['background'])
        self.page_host.pack(fill='both', expand=True)
        self.page_host.grid_rowconfigure(0, weight=1)
        self.page_host.grid_columnconfigure(0, weight=1)
        self.home_page = tk.Frame(self.page_host, bg=c['background'])
        self.home_page.grid(row=0, column=0, sticky='nsew')
        # Cards and log share a draggable split; the log takes all extra height.
        self.home_split = tk.PanedWindow(self.home_page, orient='vertical', bg=c['background'],
                                         sashwidth=8, sashrelief='flat', bd=0, showhandle=False)
        cards = tk.Frame(self.home_split, bg=c["background"])
        for column in range(3):
            cards.grid_columnconfigure(column, weight=1, uniform="jersey")

        def card(column, title):
            frame = tk.Frame(cards, bg=c["panel"], padx=10, pady=7,
                             highlightbackground="#24404A", highlightthickness=1)
            frame.grid(row=0, column=column, sticky="nsew", padx=5, pady=4)
            tk.Label(frame, text=title, bg=c["panel"], fg=c["highlight"],
                     font=("Helvetica", 12, "bold")).pack(anchor="w", pady=(0, 5))
            return frame

        radio = card(0, "Connection Status")
        tk.Label(radio, textvariable=self.radio_activity, bg=c['panel'], fg=c['connected'],
                 font=('Helvetica', 11, 'bold'), wraplength=300, justify='left').pack(anchor='w', pady=(0, 8))
        transport = tk.Frame(radio, bg=c['panel']); transport.pack(fill='x')
        for title, value in (('USB / Serial', 'serial'), ('TCP / Network', 'tcp')):
            tk.Radiobutton(transport, text=title, variable=self.connection, value=value,
                           bg=c['panel'], fg=c['text'], selectcolor='#17262b').pack(side='left')
        tk.Label(radio, text="Device path or radio IP", bg=c["panel"], fg=c["text"]).pack(anchor="w", pady=(10, 2))
        ttk.Entry(radio, textvariable=self.address, style='Desk.TEntry').pack(fill='x')
        radio_actions = tk.Frame(radio, bg=c['panel'])
        radio_actions.pack(fill='x', pady=(8, 0))
        self.styled_button(radio_actions, "Find USB Ports", self.list_ports, "#233139",
                           compact=True).pack(side='left', expand=True, fill='x', padx=(0, 3))
        self.styled_button(radio_actions, "Check Radio Connection", self.check_radio_connection,
                           "#233139", compact=True).pack(side='left', expand=True, fill='x', padx=(3, 0))
        tk.Label(radio, text="TCP checks briefly use a client slot. Close other radio clients first.",
                 bg=c['panel'], fg=c['highlight'], wraplength=275,
                 justify='left').pack(anchor='w', pady=(4, 0))
        self.connection_summary = tk.StringVar()
        tk.Label(radio, textvariable=self.connection_summary, bg=c["panel"],
                 fg=c["text"], justify="left", wraplength=280).pack(anchor="w", pady=(8, 0))
        self.connection.trace_add("write", self.refresh_connection_summary)
        self.address.trace_add("write", self.refresh_connection_summary)
        self.refresh_connection_summary()
        tk.Label(radio, text="Process status does not confirm a radio link.",
                 bg=c["panel"], fg=c["highlight"], wraplength=275,
                 justify="left").pack(anchor="w", pady=(10, 0))

        tk.Label(radio, textvariable=self.link_warning, bg=c['panel'], fg=c['highlight'], wraplength=280).pack(anchor='w', pady=3)
        tk.Label(radio, textvariable=self.centralnj_status, bg=c['panel'], fg=c['highlight'],
                 justify='left', wraplength=280).pack(anchor='w', pady=3)
        self.styled_button(radio, 'Disconnect', self.stop, c['primary']).pack(fill='x', pady=(10, 0))

        server = card(1, "BBS Server")
        self.status_row = tk.Frame(server, bg=c["panel"])
        self.status_row.pack(fill="x", pady=(0, 8))
        self.status_dot = tk.Label(self.status_row, text="●", bg=c["panel"],
                                   fg=c["primary"], font=("Helvetica", 16, "bold"))
        self.status_dot.pack(side="left", padx=(0, 6))
        tk.Label(self.status_row, textvariable=self.status, bg=c["panel"],
                 fg=c["text"], wraplength=230, justify="left").pack(side="left")
        self.status.trace_add("write", self.refresh_status_indicator)
        self.refresh_status_indicator()
        self.styled_button(server, 'Start BBS Server', self.start, '#233139').pack(fill='x', pady=3)
        self.toggle_row(server, 'Auto start at launch', self.auto_start)
        self.toggle_row(server, 'Recover after crash / disconnect', self.auto_restart)
        self.toggle_row(server, 'Demo — no radio', self.demo)
        self.styled_button(server, '↻  Restart Server', self.restart, '#233139').pack(fill='x', pady=(10, 3))
        self.styled_button(server, '■  Stop Server', self.stop, c['primary']).pack(fill='x', pady=3)
        tk.Label(server, text='Process status is separate from radio RX.', bg=c['panel'], fg='#a6b4b8',
                 wraplength=280, justify='left').pack(anchor='w', pady=8)
        for variable in (self.node_count, self.uptime, self.traffic):
            tk.Label(server, textvariable=variable, bg=c['panel'], fg=c['text']).pack(anchor='w', pady=2)

        personality = card(2, "Jersey Personality • Attitude Adjustah")
        tk.Label(personality, text='Desktop greeting style', bg=c['panel'], fg='#a6b4b8').pack(anchor='w', pady=5)
        choices = tk.Frame(personality, bg=c['panel']); choices.pack(fill='x')
        for title, mode in (('CLEAN', 'clean'), ('JERSEY', 'attitude'), ('HEAVY', 'uncensored')):
            control = self.styled_button(choices, title, lambda m=mode: self.desktop_mouth.set(m), '#233139', compact=True)
            control.pack(side='left', expand=True, fill='x', padx=2)
            def selected(*_, button=control, value=mode):
                button.configure(bg=c['primary'] if self.desktop_mouth.get() == value else '#233139')
            self.desktop_mouth.trace_add('write', selected); selected()
        self.greeting_preview = tk.StringVar()
        def preview(*_):
            self.greeting_preview.set(Personality(self.desktop_mouth.get()).message('welcome'))
        self.desktop_mouth.trace_add('write', preview); preview()
        tk.Label(personality, text='Preview', bg=c['panel'], fg=c['text']).pack(anchor='w', pady=(12, 4))
        tk.Label(personality, textvariable=self.greeting_preview, font=('Menlo', 11), bg='#020b0d', fg=c['text'],
                 wraplength=275, justify='left', anchor='nw', height=4, padx=8, pady=8,
                 highlightbackground='#1e3035', highlightthickness=1).pack(fill='x')
        self.styled_button(personality, 'Save Settings', self.save, '#233139').pack(fill='x', pady=(10, 3))
        self.styled_button(personality, 'Public Broadcast Settings', self.settings, '#233139').pack(fill='x', pady=3)
        log_section = tk.Frame(self.home_split, bg=c['background'])
        log_header = tk.Frame(log_section, bg=c["panel"], padx=12, pady=9,
                              highlightbackground="#24404A", highlightthickness=1)
        log_header.pack(fill="x", padx=20, pady=(6, 0))
        label(log_header, '▤  Server Log', 12, c['text']).pack(side='left')
        self.log_query = tk.StringVar()
        self.styled_button(log_header, 'Expand Log', self.expand_log, '#233139', compact=True).pack(side='left', padx=(12, 3))
        for title, action in (('Clear Log', self.clear_log), ('Save Log', self.export_log), ('Find', self.find_log)):
            self.styled_button(log_header, title, action, '#233139', compact=True).pack(side='right', padx=3)
        ttk.Entry(log_header, textvariable=self.log_query, width=20, style='Desk.TEntry').pack(side='right', padx=5)

        # Read-only footer: process status is not proof of a live radio link.
        footer = tk.Frame(self.home_page, bg=c["panel"], padx=14, pady=7,
                          highlightbackground="#24404A", highlightthickness=1)
        footer.pack(side="bottom", fill="x", padx=20, pady=(0, 6))
        tk.Label(footer, text="THE SITDOWN BBS  •  NORTH JERSEY",
                 bg=c["panel"], fg=c["highlight"],
                 font=("Helvetica", 12, "bold")).pack(side="left")
        self.styled_button(footer, 'The Books', self.backup, '#233139', compact=False).pack(side='left', padx=8)
        self.styled_button(footer, 'Verify Backup', self.verify_backup, '#233139', compact=False).pack(side='left', padx=4)
        self.styled_button(footer, 'Diagnostics', self.copy_diagnostics, '#233139', compact=False).pack(side='left')
        tk.Label(footer, textvariable=self.status, bg=c["panel"], fg=c["text"],
                 font=("Helvetica", 12)).pack(side="right", padx=(14, 0))
        tk.Label(footer, text="SERVER PROCESS:", bg=c["panel"], fg=c["highlight"],
                 font=("Helvetica", 12, "bold")).pack(side="right")
        self.output = tk.Text(log_section, bg="#01090b", fg="#42ed50", insertbackground=c["text"],
                              wrap="word", relief="flat", state="disabled", height=12,
                              font=("Menlo", 14), padx=16, pady=12,
                              highlightbackground="#24404A", highlightthickness=1)
        self.output.pack(fill="both", expand=True, padx=20, pady=(5, 5))
        self.home_split.pack(fill='both', expand=True)
        cards.update_idletasks()
        linespace = self.output.tk.call('font', 'metrics', self.output.cget('font'), '-linespace')
        self.home_split.add(cards, minsize=120, stretch='never')
        self.home_split.add(log_section, stretch='always',
                            minsize=log_header.winfo_reqheight() + LOG_MIN_LINES * int(linespace) + 20)
        self.output.tag_configure("event_error", foreground="#FF8B85")
        self.output.tag_configure("event_ready", foreground="#27ff3d")
        self.log(Personality(self.desktop_mouth.get(), self.public_mouth.get(), self.public_profanity.get()).message("welcome"))
        self.log(f"The Sitdown BBS {VERSION}")
        self.log("Pick a radio, or check Demo mode.")

    def update_centralnj_status(self, radio_state='unverified', mqtt_state='disabled'):
        """Update read-only operator status without claiming delivery."""
        self.centralnj_status.set(format_operator_status(radio_state, mqtt_state))

    def render_banner(self, event):
        """Avoid costly resampling for duplicate Configure events."""
        from PIL import Image, ImageTk
        size = (max(1, event.width), max(1, event.height))
        if size == self._banner_size:
            return
        self.banner_photo = ImageTk.PhotoImage(
            self.banner_source.resize(size, Image.Resampling.LANCZOS))
        self._banner_size = size
        self.banner.delete('all')
        self.banner.create_image(0, 0, anchor='nw', image=self.banner_photo)

    def toggle_row(self, parent, title, variable):
        row = tk.Frame(parent, bg=self.colors['panel']); row.pack(fill='x', pady=6)
        tk.Label(row, text=title, bg=self.colors['panel'], fg=self.colors['text']).pack(side='left')
        toggle = tk.Canvas(row, width=44, height=24, bg=self.colors['panel'], highlightthickness=0, takefocus=True, cursor='hand2')
        toggle.pack(side='right')
        def draw(*_):
            toggle.delete('all')
            color = '#16cb37' if variable.get() else '#34464d'
            toggle.create_oval(1, 2, 22, 23, fill=color, outline=color)
            toggle.create_oval(22, 2, 43, 23, fill=color, outline=color)
            toggle.create_rectangle(11, 2, 33, 23, fill=color, outline=color)
            x = 23 if variable.get() else 3
            toggle.create_oval(x, 4, x+17, 21, fill='white', outline='white')
        def change(_=None): variable.set(not variable.get())
        for event in ('<Button-1>', '<space>', '<Return>'): toggle.bind(event, change)
        toggle.bind('<FocusIn>', lambda _: toggle.configure(highlightthickness=1, highlightbackground='#ffffff'))
        toggle.bind('<FocusOut>', lambda _: toggle.configure(highlightthickness=0))
        variable.trace_add('write', draw); draw()

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
        for button in self.nav_buttons:
            button.configure(bg=self.colors['primary'] if button.page_name == title else self.colors['background'])
        if self.active_page is not None:
            self.active_page.destroy()
            self.active_page = None
        if title == 'HOME':
            self.home_page.grid(row=0, column=0, sticky='nsew')
            self.home_page.tkraise()
            return
        self.home_page.grid_remove()
        page = tk.Frame(self.page_host, bg=THEME['background'], padx=24, pady=15)
        page.grid(row=0, column=0, sticky='nsew')
        self.active_page = page
        tk.Label(page, text=title + '  /  THE SITDOWN', font=('Helvetica', 20, 'bold'),
                 bg=THEME['background'], fg=THEME['highlight']).pack(anchor='w', pady=(0, 15))
        if title == 'BBS':
            self.utility_page(page)
        elif title == 'NODES':
            self.nodes_page(page)
        elif title in ('MESSAGES', 'BOARDS'):
            self.database_page(page, title)
        elif title == 'SETTINGS':
            self.integration_settings(page)
        elif title == 'CHAT':
            self.chat_page(page)
        elif title == 'RADIO':
            for variable in (self.status, self.connection_summary, self.radio_activity, self.node_count, self.uptime):
                tk.Label(page, textvariable=variable, bg=THEME['background'], fg=THEME['text'],
                         font=('Helvetica', 14), justify='left').pack(anchor='w', pady=8)
            self.page_text(page, 'Channels from the connected radio:\n' + json.dumps(self.snapshot['channels'], indent=2))
        else:
            self.page_text(page, 'THE SITDOWN BBS\nNorth Jersey Communications Division\n\n'
                'Built for the mesh. Raised in Jersey.\nBased on TC²-BBS by TheCommsChannel. GPL-3.0.\n\n'
                'Weather: Open-Meteo (only when enabled).\nMQTT counts are separate from radio reception.\n'
                'Demo screens and build checks do not establish physical radio compatibility.')

    def page_text(self, parent, text):
        box = tk.Text(parent, bg='#0B141A', fg=THEME['text'], wrap='word',
                      font=('Menlo', 12), padx=15, pady=15, relief='flat')
        scroll = ttk.Scrollbar(parent, command=box.yview)
        scroll.pack(side='right', fill='y')
        box.configure(yscrollcommand=scroll.set)
        box.pack(fill='both', expand=True, pady=10)
        box.insert('1.0', text)
        box.configure(state='disabled')
        return box

    def utility_page(self, page):
        notebook = ttk.Notebook(page)
        notebook.pack(fill='both', expand=True)
        announcement = tk.Frame(notebook, bg=THEME['panel'], padx=20, pady=20)
        weather = tk.Frame(notebook, bg=THEME['panel'], padx=20, pady=20)
        wire = tk.Frame(notebook, bg=THEME['panel'], padx=20, pady=20)
        for frame, title in ((announcement, 'The Boss Has Spoken'), (weather, 'Jersey Weather Desk'), (wire, 'The Wire')):
            notebook.add(frame, text=title)
        tk.Label(announcement, text='Broadcast to an enabled radio channel (maximum 200 UTF-8 bytes).',
                 fg=THEME['text'], bg=THEME['panel']).pack(anchor='w')
        channel = tk.StringVar()
        options = [f"{ch['index']}: {ch['name']}" for ch in self.snapshot['channels']]
        if options:
            channel.set(options[0])
        ttk.Combobox(announcement, textvariable=channel, values=options, state='readonly').pack(fill='x', pady=10)
        text = tk.Text(announcement, height=5, bg=THEME['background'], fg=THEME['text'], insertbackground='white')
        text.pack(fill='x', pady=10)
        def send():
            message = text.get('1.0', 'end-1c')
            if not channel.get() or not message.strip() or len(message.encode('utf-8')) > 200:
                self.show_info('Check announcement', 'Choose an enabled channel and enter 1–200 UTF-8 bytes.')
                return
            if messagebox.askyesno('Broadcast announcement?', f'Channel {channel.get()}\n\n{message}', parent=self.root):
                self.control_request('announce', channel=int(channel.get().split(':', 1)[0]), text=message)
        self.styled_button(announcement, 'PREVIEW & BROADCAST', send).pack(anchor='w')
        tk.Label(announcement, text='Channel list updates when you reopen this tab after connecting.\n'
                 'Remote /announce commands require an administrator ID in Settings.',
                 bg=THEME['panel'], fg=THEME['highlight'], justify='left').pack(anchor='w', pady=15)
        self.styled_button(weather, 'GET WEATHER REPORT', lambda: self.control_request('weather')).pack(anchor='w')
        config = self.read_config()
        enabled = config.getboolean('weather', 'internet_enabled', fallback=False)
        self.page_text(weather, 'Internet weather: ' + ('Enabled' if enabled else 'Disabled') + '\n\n'
                       + config.get('weather', 'local_report', fallback='No local report configured.')
                       + '\n\nConfigure a local report or explicitly enable internet weather in Settings.\n'
                       'Internet results use Open-Meteo, cache for ten minutes, and label stale data.')
        wire_tabs = ttk.Notebook(wire)
        wire_tabs.pack(fill='both', expand=True)
        counts = tk.Frame(wire_tabs, bg=THEME['panel'], padx=10, pady=10)
        wire_tabs.add(counts, text='Server counts')
        self.wire_views = {}
        for network, label in NETWORK_LABELS.items():
            frame = tk.Frame(wire_tabs, bg=THEME['panel'], padx=10, pady=10)
            wire_tabs.add(frame, text=label)
            self.wire_views[network] = self.page_text(frame, self.render_wire(network))
        self.styled_button(counts, 'REFRESH COUNTS', lambda: self.select_page('BBS')).pack(anchor='w')
        data = self.snapshot['mqtt']
        self.page_text(counts, f"MQTT: {data['status']}\nMessages this server session: {data['total']}\n\n"
                       + '\n'.join(f'{count}  {topic}' for topic, count in data['topics'])
                       + '\n\nMQTT is opt-in and never forwards messages to the radio.\n'
                       'Counts exclude retained messages; they are not proof of radio RX.')

    def nodes_page(self, page):
        query = tk.StringVar()
        tk.Label(page, text='Search the full radio node database by ID, short name, or long name.\n'
                 'Snapshot remains visible after disconnect; reopen this tab to refresh.',
                 bg=THEME['background'], fg=THEME['text'], justify='left').pack(anchor='w')
        tk.Entry(page, textvariable=query, bg=THEME['panel'], fg=THEME['text'],
                 insertbackground='white').pack(fill='x', pady=10)
        box = self.page_text(page, '')
        def refresh(*_):
            from sitdown_utilities import node_id
            key = query.get().strip().casefold()
            rows = [row for row in self.snapshot['nodes'] if not key or node_id(key) == row['id']
                    or key in (row['shortName'] + ' ' + row['longName']).casefold()]
            text = '\n\n'.join(format_node(row) for row in rows) or 'No matching nodes. Connect a radio to load its database.'
            box.configure(state='normal'); box.delete('1.0', 'end'); box.insert('1.0', text); box.configure(state='disabled')
        query.trace_add('write', refresh)
        refresh()

    def database_page(self, page, title):
        self.styled_button(page, 'REFRESH', lambda: self.select_page(title)).pack(anchor='w')
        path = self.config_path().parent / 'bulletins.db'
        text = 'No local database yet. Start the BBS to initialize it.'
        if self.demo.get():
            text = 'DEMO DATA — no radio connected\n\n' + ('Welcome to the Social Club\nGeneral: Stay local, stay connected.'
                    if title == 'BOARDS' else 'Sample mail\nAyyy, pull up a chair.')
        elif path.exists():
            try:
                with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
                    sql = ('SELECT board, sender_short_name, date, subject, content FROM bulletins ORDER BY id DESC LIMIT 100'
                           if title == 'BOARDS' else 'SELECT sender_short_name, recipient, date, subject, content FROM mail ORDER BY id DESC LIMIT 100')
                    rows = db.execute(sql).fetchall()
                text = '\n\n'.join('\n'.join(str(value) for value in row) for row in rows) or 'No messages yet.'
            except sqlite3.Error as exc:
                text = 'Could not read the local database: ' + str(exc)
        self.page_text(page, 'Local operator view — latest 100 records, read only.\n\n' + text)

    def chat_channel_names(self):
        """Radio channels from the last snapshot plus any channel that has messages."""
        names = {ch['index']: ch['name'] for ch in self.snapshot.get('channels', [])
                 if isinstance(ch, dict) and isinstance(ch.get('index'), int)}
        for index in self.chat_log.channels():
            names.setdefault(index, f'Channel {index}')
        return dict(sorted(names.items())) or {0: 'Primary'}

    def chat_page(self, page):
        sending = ('Sending is ON for this session.' if self.centralnj_send_enabled.get() else
                   "Sending is OFF — turn on 'Allow operator send preflight' in Settings to send.")
        tk.Label(page, text='Channel chat on your BBS radio. The BBS keeps answering direct-message '
                 'commands.\n' + sending + ' History is kept between restarts.',
                 bg=THEME['background'], fg=THEME['text'], justify='left').pack(anchor='w')
        self.styled_button(page, 'CLEAR HISTORY', self.clear_chat_history, THEME['panel'],
                           compact=True).pack(anchor='e')
        tabs = ttk.Notebook(page)
        tabs.pack(fill='both', expand=True, pady=(6, 0))
        self.chat_views = {}
        self.chat_tabs, self.chat_tab_frames = tabs, {}
        for index, name in self.chat_channel_names().items():
            frame = tk.Frame(tabs, bg=THEME['panel'], padx=10, pady=10)
            tabs.add(frame, text=name)
            self.chat_tab_frames[index] = frame
            compose = tk.Frame(frame, bg=THEME['panel'])
            compose.pack(side='bottom', fill='x', pady=(6, 0))
            draft = tk.StringVar()
            counter = tk.StringVar(value='0/200 bytes')
            entry = ttk.Entry(compose, textvariable=draft, style='Desk.TEntry')
            send = lambda _event=None, i=index, n=name, d=draft: self.send_chat(i, n, d)
            self.styled_button(compose, 'SEND', send, THEME['primary'], compact=True).pack(side='right', padx=(6, 0))
            tk.Label(compose, textvariable=counter, bg=THEME['panel'], fg=THEME['highlight']).pack(side='right', padx=6)
            entry.pack(side='left', fill='x', expand=True)
            entry.bind('<Return>', send)
            draft.trace_add('write', lambda *_, d=draft, c=counter: c.set(f"{len(d.get().encode('utf-8'))}/200 bytes"))
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
        if not event.get('mine') and event.get('channel') != self.visible_chat_channel():
            self.chat_unread[event['channel']] += 1
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
        name = self.chat_channel_names().get(sent['channel'], f"Channel {sent['channel']}")
        text = sent['text'] if len(sent['text']) <= 40 else sent['text'][:39] + '…'
        self.log(f'Chat {name}: {status_label(sent)} — "{text}"')

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

    def refresh_chat_views(self):
        """Redraw visible chat tabs whose text changed, keeping the reader near the newest line."""
        for index, (name, box) in self.chat_views.items():
            if not box.winfo_exists():
                continue
            text = self.chat_log.render(index, name)
            if box.get('1.0', 'end-1c') == text:
                continue
            at_bottom = box.yview()[1] >= 0.999
            box.configure(state='normal')
            box.delete('1.0', 'end')
            box.insert('1.0', text)
            box.configure(state='disabled')
            if at_bottom:
                box.see('end')

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
        if not connect:
            if self.dual_mqtt is not None:
                self.dual_mqtt.stop(network)
            self.show_info('Mesh Networks', f'{network}: disconnected.')
            return
        if self.demo.get():
            self.show_info('Mesh Networks', 'Demo mode does not connect to brokers.')
            return
        if self.dual_mqtt is None:
            self.dual_mqtt = DualMQTTMonitors(self.read_config())
        ok = self.dual_mqtt.connect(network)
        status = self.dual_mqtt.snapshot()[network]['status']
        self.show_info('Mesh Networks', f'{network}: {status}. ' +
                       ('Receive-only connection requested.' if ok else 'Check saved settings.'))

    def integration_settings(self, page):
        config = self.read_config()
        tabs = ttk.Notebook(page)
        fields = {}
        self.setting_fields = fields
        sections = {
            'startup': [('enabled', 'Broadcast on server start', 'bool'), ('channel', 'Channel index', '0'), ('message', 'Custom welcome (blank = personality)', '')],
            'announcements': [('admin_nodes', 'Remote administrator node IDs (comma separated)', '')],
            'weather': [('internet_enabled', 'Enable internet weather', 'bool'), ('local_report', 'Offline report (include time)', ''), ('latitude', 'Latitude', '40.20'), ('longitude', 'Longitude', '-74.50')],
            'mqtt_monitor': [('enabled', 'Enable MQTT counter', 'bool'), ('host', 'Broker hostname', ''), ('topic', 'Subscription topic', ''), ('tls', 'Use TLS', 'bool_true'), ('port', 'Port', '8883'), ('username', 'Username', ''), ('password', 'Password', '')],
            'mqtt_centralnj': [('enabled', 'Enable CentralNJ monitor', 'bool'), ('auto_connect', 'Auto-connect CentralNJ at startup', 'bool'), ('host', 'Broker hostname', 'mqtt.meshtastic.org'), ('topic', 'Topics (up to 4, comma-separated): exact, or msh/<region>/2/e/<channel>/+', ''), ('tls', 'Use TLS', 'bool_true'), ('port', 'Port', '8883'), ('username', 'Username', ''), ('password', 'Password', '')],
            'mqtt_sjmesh': [('enabled', 'Enable SJMesh monitor', 'bool'), ('auto_connect', 'Auto-connect SJMesh at startup', 'bool'), ('host', 'Broker hostname', 'mqtt.sjmesh.net'), ('topic', 'Topics (up to 4, comma-separated): exact, or msh/<region>/2/e/<channel>/+', ''), ('tls', 'Use TLS', 'bool_true'), ('port', 'Port', '8883'), ('username', 'Username', ''), ('password', 'Password', '')],
            'personality': [('public_mode', 'Public greeting: clean / attitude / uncensored', 'clean'), ('allow_public_profanity', 'Allow profanity in generated public greetings', 'bool')],
        }
        for section, entries in sections.items():
            holder = tk.Frame(tabs, bg=THEME['panel'])
            frame = self.scrollable(holder)
            frame.configure(padx=20, pady=12)
            frame.grid_columnconfigure(1, weight=1)
            tabs.add(holder, text={'mqtt_monitor': 'MQTT', 'mqtt_centralnj': 'CentralNJ', 'mqtt_sjmesh': 'SJMesh', 'startup': 'Startup', 'announcements': 'Admins', 'weather': 'Weather', 'personality': 'Personality'}[section])
            # One row per setting (label beside its field) so every tab fits on screen.
            for row, (key, label, default) in enumerate(entries):
                if default.startswith('bool'):
                    var = tk.BooleanVar(value=config.getboolean(section, key, fallback=default == 'bool_true'))
                    tk.Checkbutton(frame, text=label, variable=var, bg=THEME['panel'], fg=THEME['text'],
                                   selectcolor=THEME['background']).grid(row=row, column=0, columnspan=2, sticky='w', pady=3)
                else:
                    var = tk.StringVar(value=config.get(section, key, fallback=default))
                    tk.Label(frame, text=label, bg=THEME['panel'], fg=THEME['text'], wraplength=300,
                             justify='left').grid(row=row, column=0, sticky='w', padx=(0, 14), pady=3)
                    ttk.Entry(frame, textvariable=var, show='*' if key == 'password' else '',
                              style='Desk.TEntry').grid(row=row, column=1, sticky='ew', pady=3)
                fields[(section, key)] = var
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
                path = self.config_path()
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix('.ini.tmp')
                with temporary.open('w') as stream:
                    config.write(stream)
                temporary.chmod(0o600)
                temporary.replace(path)
                self.show_info('Settings saved', 'Restart the server to apply these settings.')
            except (ValueError, OSError) as exc:
                self.show_info('Settings not saved', str(exc))
        guide = (
                    "CENTRALNJ / TWO-WAY RADIO SETUP\n"
                    "1. Configure the CentralNJ channel and its PSK on your Meshtastic radio.\n"
                    "2. Configure the radio MQTT broker: mqtt.meshtastic.org.\n"
                    "3. Enter broker credentials on the radio, not in a public repository.\n"
                    "4. Enable MQTT uplink and downlink for the CentralNJ channel on the radio.\n"
                    "5. Set the radio MQTT root topic to the correct regional topic.\n"
                    "6. Connect the radio to this BBS by USB or TCP and test with a small message.\n\n"
                    "The MQTT counter above is separate: it only counts incoming broker "
                    "traffic and cannot decrypt or send Meshtastic messages. "
                    "Leave it disabled unless you explicitly want monitoring. "
                    "Avoid subscribing broadly to msh/US/# on limited hardware; "
                    "start with the narrowest authorized topic. "
                    "Two-way delivery requires radio and channel configuration; "
                    "this screen does not activate radio MQTT automatically."
                )
        def check_mqtt_configuration():
            """Validate current unsaved form values offline, without opening a connection."""
            preview = configparser.ConfigParser()
            preview.add_section('mqtt_monitor')
            for key, _label, _default in sections['mqtt_monitor']:
                var = fields[('mqtt_monitor', key)]
                preview.set('mqtt_monitor', key,
                            str(var.get()).lower() if isinstance(var, tk.BooleanVar) else var.get())
            report = ActivityCounter(preview).diagnostics()
            self.show_info('MQTT Configuration Check', report)

        self.toggle_row(page, 'Allow operator send preflight (off by default)', self.centralnj_send_enabled)
        # Compact action bar: three rows instead of a tall stack that squeezed the form.
        actions = tk.Frame(page, bg=THEME['background'])
        actions.pack(side='bottom', fill='x', pady=(6, 0))
        rows = (
            [('SHOW MQTT NETWORK STATUS', self.mqtt_network_status, THEME['primary'])]
            + [(f'{verb} {name.upper()}', lambda n=name, c=(verb == 'CONNECT'): self.mqtt_network_action(n, c), THEME['panel'])
               for name in ('centralnj', 'sjmesh') for verb in ('CONNECT', 'DISCONNECT')],
            [('CENTRALNJ SETUP GUIDE', lambda: self.show_info('CentralNJ MQTT / Radio Setup', guide), THEME['primary']),
             ('CHECK MQTT CONFIGURATION', check_mqtt_configuration, THEME['panel']),
             ('CHECK SEND SAFETY', self.check_centralnj_send_safety, THEME['panel'])],
            [('SAVE — APPLY ON NEXT SERVER START', save, THEME['connected'])],
        )
        for buttons in rows:
            line = tk.Frame(actions, bg=THEME['background'])
            line.pack(fill='x', pady=2)
            for text, command, color in buttons:
                self.styled_button(line, text, command, color, compact=True).pack(
                    side='left', expand=True, fill='x', padx=2)
        # Pack the expandable notebook last so footer controls stay visible on Intel Mac.
        tabs.pack(side='top', fill='both', expand=True)

    def scrollable(self, parent):
        """Return a frame inside parent that scrolls vertically only when it does not fit."""
        canvas = tk.Canvas(parent, bg=THEME['panel'], highlightthickness=0, bd=0)
        bar = ttk.Scrollbar(parent, orient='vertical', command=canvas.yview)
        inner = tk.Frame(canvas, bg=THEME['panel'])
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

    def check_centralnj_send_safety(self):
        """Offline preflight only; never sends a packet or publishes MQTT."""
        allowed, reason = send_preflight(enabled=self.centralnj_send_enabled.get(),
                                          confirmed=False, radio_ready=False,
                                          demo_mode=self.demo.get(), message='')
        self.show_info('CentralNJ Send Safety', reason)
        return allowed

    def layout_navigation(self, event):
        """Wrap measured controls so every action survives font/window resizing."""
        if not self.nav_buttons:
            return
        cell_width = max(control.winfo_reqwidth() for control in self.nav_buttons) + 6
        columns = max(1, min(len(self.nav_buttons), event.width // cell_width))
        if columns == self._nav_columns:
            return
        self._nav_columns = columns
        for column in range(len(self.nav_buttons)):
            self.navigation.grid_columnconfigure(column, weight=1 if column < columns else 0,
                                                 uniform='nav' if column < columns else '')
        for index, control in enumerate(self.nav_buttons):
            row, column = divmod(index, columns)
            control.grid(row=row, column=column, sticky="ew", padx=3, pady=2)

    @staticmethod
    def draw_skyline(canvas, width, height):
        """Original vector skyline and suspension bridge, inspired by the app icon."""
        canvas.delete("all")
        def line(points, color, thickness=1):
            canvas.create_line(*[value * (width if i % 2 == 0 else height)
                                 for i, value in enumerate(points)],
                               fill=color, width=thickness)
        for index, top in enumerate((.48, .3, .55, .2, .4, .32, .5, .16, .38, .25, .52)):
            left = index / 11
            right = left + .065
            canvas.create_rectangle(left * width, top * height, right * width,
                                    .88 * height, fill="#10181D", outline="")
            for level in range(3):
                y = top + .1 + level * .12
                if y < .84:
                    line((left + .02, y, right - .02, y), "#A47D3D", 2)
        for tower in (.22, .78):
            line((tower, .12, tower, .9), "#C8C8BD", 3)
        cables = ((.02, .62), (.22, .18), (.5, .58), (.78, .18), (.98, .62))
        for (x1, y1), (x2, y2) in zip(cables, cables[1:]):
            line((x1, y1, x2, y2), "#C8C8BD", 2)
            for step in range(1, 6):
                t = step / 6
                x, y = x1 + (x2 - x1) * t, y1 + (y2 - y1) * t
                line((x, y, x, .88), "#64747B")
        line((0, .88, 1, .88), THEME["highlight"], 3)
        # Layer the identity over the skyline rather than shrinking it into a side panel.
        title_font = ("Helvetica", max(22, min(47, int(width / 24))), "bold")
        # Aqua Tk does not render stippled fills transparently; keep skyline visible.
        canvas.create_rectangle(0, 0, width * .52, height * .86, fill='#071017', outline='')
        canvas.create_text(width * .035, height * .2, text='THE', fill='#F5F5F0', anchor='w',
                           font=('Helvetica', max(12, int(width / 70)), 'bold'))
        x = width * .035
        for word, color in (('SIT', '#008C45'), ('DOWN', '#F5F5F0'), (' BBS', '#E13232')):
            item = canvas.create_text(x, height * .5, text=word, fill=color, anchor='w', font=title_font)
            x = canvas.bbox(item)[2]
        canvas.create_text(width * .038, height * .78,
                           text='NORTH JERSEY  •  COMMUNICATIONS DIVISION',
                           fill='#F5F5F0', anchor='w',
                           font=('Helvetica', max(9, min(13, int(width / 82))), 'bold'))


    def show_info(self, title, message):
        """Non-blocking themed information window; server polling stays active."""
        previous_focus = self.root.focus_get()
        win = tk.Toplevel(self.root)
        win.title(title)
        win.transient(self.root)
        win.configure(bg=self.colors["background"])
        win.resizable(False, False)
        tk.Frame(win, bg=self.colors["connected"], height=4).pack(fill="x")
        tk.Label(win, text=title, bg=self.colors["background"],
                 fg=self.colors["highlight"], font=("Helvetica", 16, "bold"),
                 wraplength=440, justify="left").pack(anchor="w", padx=22, pady=(18, 12))
        tk.Label(win, text=message, bg=self.colors["background"],
                 fg=self.colors["text"], font=("Helvetica", 12),
                 wraplength=440, justify="left").pack(anchor="w", padx=22, pady=(0, 18))
        def dismiss(_event=None):
            win.destroy()
            if previous_focus is not None and previous_focus.winfo_exists():
                previous_focus.focus_set()
            return "break"
        close = self.styled_button(win, "OK", dismiss, self.colors["primary"])
        close.pack(fill="x", padx=22, pady=(0, 18))
        win.bind("<Escape>", dismiss)
        win.bind("<Return>", dismiss)
        win.protocol("WM_DELETE_WINDOW", dismiss)
        win.update_idletasks()
        x = max(0, self.root.winfo_rootx() + (self.root.winfo_width() - win.winfo_reqwidth()) // 2)
        y = max(0, self.root.winfo_rooty() + (self.root.winfo_height() - win.winfo_reqheight()) // 2)
        win.geometry(f"+{x}+{y}")
        close.focus_set()
        return win

    def refresh_connection_summary(self, *_args):
        """Display configured endpoint without claiming it is connected."""
        kind = "TCP / Network" if self.connection.get() == "tcp" else "USB / Serial"
        endpoint = self.address.get().strip() or "Not specified (auto/default)"
        self.connection_summary.set(f"Configured: {kind}\nEndpoint: {endpoint}")

    def refresh_status_indicator(self, *_args):
        """Color denotes BBS process state, not a verified radio link."""
        state = self.status.get().lower()
        if "demo" in state or "starting" in state or "retry" in state:
            color = THEME["highlight"]
        elif "running" in state or "initialized" in state:
            color = THEME["connected"]
        else:
            color = THEME["primary"]
        self.status_dot.configure(fg=color)

    def show_home(self):
        self.select_page("HOME")
        """Home is the main live dashboard."""
        self.log("HOME: Dashboard is already open.")

    def show_command_info(self, shortcut):
        """Show the actual radio command; do not imply desktop mail is implemented."""
        self.show_info("The Sitdown BBS — Radio Commands",
                            f"Send {shortcut} as a direct message to your BBS node.\n"
                            "This desktop build does not provide a direct message composer.")

    def show_radio(self):
        """Bring attention to real radio connection settings on the dashboard."""
        self.show_info("Radio Connection",
                            "Choose USB / Serial or Network radio on the dashboard, "
                            "then enter the device path or radio IP address. "
                            "Start the Sitdown to launch the configured BBS. "
                            "A running server process does not prove a radio link.")

    def show_about(self):
        self.show_info(
            "About The Sitdown BBS",
            f"THE SITDOWN BBS {VERSION} — North Jersey Communications Division\n"
            "Jersey-themed Meshtastic bulletin board.\n"
            "Built for the mesh. Raised in Jersey.\n\n"
            "Original project: TC²-BBS by TheCommsChannel.\n"
            "https://github.com/TheCommsChannel/TC2-BBS-mesh\n\n"
            "Connection details shown here come from the running app, "
            "not from promotional demo artwork."
        )

    def styled_button(self, parent, text, command, color=None, compact=False):
        # macOS Aqua ignores custom backgrounds on native Tk buttons,
        # producing white-on-white labels. Use a keyboard-accessible Label.
        background = color or self.colors["primary"]
        control = tk.Label(parent, text=text, bg=background, fg="#FFFFFF",
                           padx=8 if compact else 13, pady=7,
                           cursor="hand2",
                           font=("Helvetica", 10 if compact else 12, "bold"), relief="flat",
                           highlightthickness=2, highlightbackground=self.colors["background"],
                           takefocus=True)
        control.bind("<Button-1>", lambda _event: command())
        control.bind("<Return>", lambda _event: command())
        control.bind("<space>", lambda _event: command())
        control.bind("<FocusIn>", lambda _event: control.configure(
            highlightbackground=self.colors["highlight"]))
        control.bind("<FocusOut>", lambda _event: control.configure(
            highlightbackground=self.colors["background"]))
        return control

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
        window = tk.Toplevel(self.root)
        self._expanded_log_window = window
        window.title("THE SITDOWN BBS — Live Server Log")
        window.geometry("1000x650")
        window.minsize(550, 300)
        frame = tk.Frame(window, bg="#111e22", padx=12, pady=12)
        frame.pack(fill="both", expand=True)
        scrollbar = tk.Scrollbar(frame)
        scrollbar.pack(side="right", fill="y")
        viewer = tk.Text(frame, bg="#01090b", fg="#42ed50", font=("Menlo", 14),
                         wrap="word", padx=16, pady=12, state="disabled",
                         yscrollcommand=scrollbar.set)
        viewer.pack(side="left", fill="both", expand=True)
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
            messagebox.showerror("Couldn't save log", str(exc))
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
            messagebox.showerror("Need a Guy?", "Missing config.ini. Copy example_config.ini to config.ini and configure it first.")
            return
        addr = self.address.get().strip()
        if self.connection.get() == "tcp" and not addr:
            messagebox.showerror("Need a Guy?", "Enter the radio IP or hostname for TCP.")
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
            self.link_warning.set("Radio link: Unverified — awaiting SDK status")
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
                    self.link_warning.set("Radio disconnected — awaiting SDK recovery (30s)" if value == "disconnected" else "SDK session established; continuous link unverified")
                elif kind == "control":
                    if value.get('type') == 'snapshot':
                        self.snapshot = value
                        self.node_count.set(f"Known nodes: {len(value.get('nodes') or [])}")
                    elif value.get('type') == 'chat':
                        if self.chat_log.add(value):
                            self.note_chat_message(value)
                    elif value.get('type') == 'chat_status':
                        sent = self.chat_log.set_status(value.get('id'), value.get('status'), value.get('reason'))
                        if sent is not None:
                            self.log_chat_status(sent)
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
                    self.link_warning.set("Radio disconnected — awaiting SDK recovery (30s)")
                if monitor.last_rx_at is not None:
                    age = max(0, int(time.time() - monitor.last_rx_at))
                    self.radio_activity.set(f"Radio RX observed {age}s ago")
                self.uptime.set(f"Uptime: {monitor.uptime_seconds()}s")
            elif not monitor.stopping and self.restart_at is None:
                code = monitor.process.poll() if monitor.process else "unknown"
                self.log(f"BBS process exited ({code}).")
                self.status.set("Disconnected / process stopped")
                self.link_warning.set("Radio link: Process stopped — unverified")
                self.schedule_recovery()
                self.supervisor = None
                self.radio_activity.set("Radio activity: Process stopped")
        self.drain_mqtt_events()
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
        self.link_warning.set("Radio link: Unverified — server stopped")
        if hasattr(self, "radio_activity"):
            self.radio_activity.set("Radio activity: Process stopped")
        self.uptime.set("Uptime: 0s")
        self.log("Fuggedaboutit. Server stopped.")

    def commands(self):
        win = tk.Toplevel(self.root)
        win.title("The Playbook | Radio Commands")
        win.configure(bg=THEME["background"])
        tk.Label(win, text="DM these commands to your BBS radio", bg=THEME["background"],
                 fg=THEME["highlight"], font=("Helvetica", 14, "bold")).pack(padx=20, pady=12)
        selected = tk.StringVar(value=COMMANDS[0][0])
        for cmd, desc in COMMANDS:
            tk.Radiobutton(win, text=f"{cmd} — {desc}", value=cmd, variable=selected,
                           bg=THEME["background"], fg=THEME["text"],
                           selectcolor=THEME["panel"], anchor="w").pack(fill="x", padx=20)
        def copy():
            win.clipboard_clear()
            win.clipboard_append(selected.get())
            win.update()
        self.styled_button(win, "Copy selected command", copy, THEME["primary"]).pack(pady=15)

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
            create_backup_archive(destination, [CONFIG, self.config_path()], sorted(database_paths))
        except (OSError, ValueError, sqlite3.DatabaseError, zipfile.BadZipFile) as exc:
            self.log(f"The Books: backup failed: {exc}")
            self.show_info("Backup failed", f"No new backup was saved. {exc}")
            return
        self.log("The Books: verified backup saved. Treat backups as sensitive; they may contain messages and keys.")

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
        with tempfile.TemporaryDirectory() as folder:
            DATA = Path(folder)
            CONFIG = DATA / 'launcher.json'
            root = tk.Tk()
            app = SitdownApp(root)
            app.demo.set(True)
            app.start()
            for page in ('BBS', 'MESSAGES', 'BOARDS', 'NODES', 'SETTINGS', 'HOME'):
                app.select_page(page)
                root.update()
                if page == 'HOME':
                    assert app.home_page.winfo_ismapped(), 'Home dashboard did not reappear'
                    assert app.active_page is None, 'Other page still active on Home'
                    assert app.home_page.winfo_children(), 'Home dashboard controls disappeared'
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
