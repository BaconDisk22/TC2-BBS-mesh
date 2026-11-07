"""The Sitdown design system for the Tk desktop.

One place for colour tokens (dark and light), the type scale, spacing and the
reusable controls every screen is built from. Screens never hard-code colours
or fonts: they ask for a role ("surface", "muted", "danger") or a component
(button, card, toggle, field, pill, notice).

The look follows platform conventions (clear hierarchy, generous spacing,
one accent colour, state never shown by colour alone) with the Jersey
tricolour kept as the brand accent. There are no animations; hover and focus
changes are instant, which also respects reduced-motion preferences.
"""
import re
import sys
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

# Semantic colour roles. Each palette is checked for readable contrast:
# text on surface >= 7:1, muted on surface >= 4.5:1, white on accent >= 4.5:1.
PALETTES = {
    'dark': {
        'bg': '#0F1318', 'sidebar': '#0B0E12', 'surface': '#171C23', 'surface_alt': '#1E242D',
        'raised': '#262E39', 'border': '#2C3542', 'text': '#E9ECF1', 'muted': '#A3ADBB',
        'subtle': '#7D8796', 'accent': '#C8202F', 'accent_hover': '#DA2E3D', 'on_accent': '#FFFFFF',
        'success': '#3DC47E', 'warning': '#E8AE45', 'danger': '#F6767E', 'info': '#5AA6FF',
        'focus': '#7CB8FF', 'field': '#10151B', 'log_bg': '#0B0F14', 'log_text': '#C6F2D0',
        'brand_green': '#1FA05A', 'brand_white': '#F5F5F0', 'brand_red': '#E13232', 'selection': '#2B4A73',
        'nav_selected': '#262E39',
    },
    'light': {
        'bg': '#F3F4F6', 'sidebar': '#E8EAEE', 'surface': '#FFFFFF', 'surface_alt': '#F5F6F8',
        'raised': '#E6E9EE', 'border': '#D3D8DF', 'text': '#1A1E24', 'muted': '#535D6B',
        'subtle': '#5C6571', 'accent': '#C11D2C', 'accent_hover': '#A81825', 'on_accent': '#FFFFFF',
        'success': '#1B7F4B', 'warning': '#9A6200', 'danger': '#B92B34', 'info': '#1D63C2',
        'focus': '#1D63C2', 'field': '#FFFFFF', 'log_bg': '#FBFBFC', 'log_text': '#1D3A26',
        'brand_green': '#00843D', 'brand_white': '#1A1E24', 'brand_red': '#C11D2C', 'selection': '#CFE0F7',
        'nav_selected': '#D5DAE2',
    },
}

# Type scale in points before the user's text-size setting is applied.
TYPE = {'display': 22, 'title': 17, 'heading': 13, 'body': 12, 'small': 11, 'mono': 12, 'button': 12, 'nav': 12}
TEXT_SIZES = {'small': 0.9, 'default': 1.0, 'large': 1.15, 'larger': 1.3}
SPACE = {'xs': 4, 'sm': 8, 'md': 12, 'lg': 16, 'xl': 24, 'xxl': 32}

STATES = ('ok', 'busy', 'warn', 'error', 'idle')
STATE_SYMBOL = {'ok': '●', 'busy': '◐', 'warn': '▲', 'error': '■', 'idle': '○'}


def contrast(foreground, background):
    """WCAG contrast ratio between two #rrggbb colours."""
    def luminance(hex_colour):
        channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
    high, low = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def state_of(text):
    """Map a human status line to a visual state. Colour is always paired with a symbol and words."""
    value = (text or '').lower()
    if any(word in value for word in ('fail', 'error', 'refused', 'unavailable', 'exited', 'crash', 'disconnected')):
        return 'error'
    if value.strip() == 'off' or any(word in value for word in ('not connected', 'stopped', ': off', 'cancelled')):
        return 'idle'
    if any(word in value for word in ('starting', 'retry', 'recovery', 'connecting', 'awaiting', 'requested',
                                      'reconnecting')):
        return 'busy'
    if any(word in value for word in ('demo', 'unverified', 'not yet', 'cancel')):
        return 'warn'
    if any(word in value for word in ('running', 'connected', 'established', 'observed', 'initialized',
                                      'subscription', 'ready', 'live', 'listening')):
        return 'ok'
    return 'idle'


def is_mac():
    return sys.platform == 'darwin'


class UI:
    """Tokens plus component factories bound to one Tk root."""

    def __init__(self, root, mode='dark', text_size='default'):
        self.root = root
        self.mode = mode if mode in PALETTES else 'dark'
        self.text_size = text_size if text_size in TEXT_SIZES else 'default'
        self.c = dict(PALETTES[self.mode])
        self.fonts = {}
        self._make_fonts()
        self.style_ttk()

    # ---- tokens -------------------------------------------------------
    def _make_fonts(self):
        family = tkfont.nametofont('TkDefaultFont', root=self.root).actual('family')
        mono = 'Menlo' if is_mac() else tkfont.nametofont('TkFixedFont', root=self.root).actual('family')
        weights = {'display': 'bold', 'title': 'bold', 'heading': 'bold', 'button': 'bold', 'nav': 'normal'}
        for role, size in TYPE.items():
            name = f'Sitdown.{role}'
            try:
                found = tkfont.nametofont(name, root=self.root)
            except tk.TclError:
                found = tkfont.Font(root=self.root, name=name, exists=False)
            found.configure(family=mono if role == 'mono' else family, weight=weights.get(role, 'normal'))
            self.fonts[role] = found
        self.apply_text_size(self.text_size)

    def apply_text_size(self, text_size):
        """Resize every named font at once; widgets using them update in place."""
        self.text_size = text_size if text_size in TEXT_SIZES else 'default'
        scale = TEXT_SIZES[self.text_size]
        for role, size in TYPE.items():
            self.fonts[role].configure(size=round(size * scale))

    def font(self, role):
        return self.fonts[role]

    def style_ttk(self):
        c = self.c
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', background=c['bg'], foreground=c['text'], font=self.fonts['body'])
        edges = dict(bordercolor=c['border'], lightcolor=c['border'], darkcolor=c['border'])
        style.configure('TNotebook', background=c['bg'], borderwidth=1, tabmargins=(0, 4, 0, 0), **edges)
        style.configure('TNotebook.Tab', background=c['surface_alt'], foreground=c['muted'], borderwidth=1,
                        padding=(14, 7), font=self.fonts['small'], focuscolor=c['focus'], **edges)
        style.map('TNotebook.Tab', background=[('selected', c['surface']), ('active', c['raised'])],
                  foreground=[('selected', c['text']), ('active', c['text'])])
        style.configure('Card.TNotebook', background=c['surface'])
        for name in ('Desk.TEntry', 'TEntry'):
            style.configure(name, fieldbackground=c['field'], foreground=c['text'], insertcolor=c['text'],
                            bordercolor=c['border'], lightcolor=c['border'], darkcolor=c['border'], padding=7)
            style.map(name, bordercolor=[('focus', c['focus'])], lightcolor=[('focus', c['focus'])])
        style.configure('TCombobox', fieldbackground=c['field'], background=c['raised'], foreground=c['text'],
                        arrowcolor=c['text'], bordercolor=c['border'], padding=6)
        style.map('TCombobox', fieldbackground=[('readonly', c['field'])], foreground=[('readonly', c['text'])],
                  bordercolor=[('focus', c['focus'])])
        for name in ('TScrollbar', 'Vertical.TScrollbar'):
            style.configure(name, background=c['raised'], troughcolor=c['field'], bordercolor=c['field'],
                            lightcolor=c['raised'], darkcolor=c['raised'], arrowcolor=c['muted'], gripcount=0,
                            relief='flat')
            style.map(name, background=[('active', c['border'])])
        self.root.option_add('*TCombobox*Listbox.background', c['field'])
        self.root.option_add('*TCombobox*Listbox.foreground', c['text'])
        self.root.option_add('*TCombobox*Listbox.selectBackground', c['selection'])

    # ---- primitives ----------------------------------------------------
    def frame(self, parent, bg='bg', **kw):
        return tk.Frame(parent, bg=self.c[bg], **kw)

    def label(self, parent, text='', role='body', color='text', bg='bg', wrap=None, **kw):
        options = dict(text=text, font=self.fonts[role], fg=self.c[color], bg=self.c[bg], justify='left', anchor='w')
        if wrap:
            options['wraplength'] = wrap
        options.update(kw)
        return tk.Label(parent, **options)

    def button(self, parent, text, command, kind='secondary', small=False, bg='surface'):
        """A keyboard-accessible button. Labels are used because macOS ignores native button colours."""
        c = self.c
        colours = {
            'primary': (c['accent'], c['on_accent'], c['accent_hover']),
            'secondary': (c['raised'], c['text'], c['border']),
            'danger': (c['raised'], c['danger'], c['border']),
            'ghost': (c[bg], c['muted'], c['raised']),
        }
        normal, foreground, hover = colours.get(kind, colours['secondary'])
        control = tk.Label(parent, text=text, bg=normal, fg=foreground, cursor='hand2', takefocus=True,
                           font=self.fonts['small' if small else 'button'],
                           padx=SPACE['md'] if small else SPACE['lg'], pady=SPACE['xs'] + (1 if small else 4),
                           highlightthickness=2, highlightbackground=c[bg], highlightcolor=c['focus'])
        control.kind, control.normal_bg = kind, normal

        def run(_event=None):
            if str(control.cget('state')) != 'disabled':
                command()
            return 'break'
        control.bind('<Button-1>', run)
        control.bind('<Return>', run)
        control.bind('<space>', run)
        control.bind('<Enter>', lambda _e: control.configure(bg=hover) if str(control.cget('state')) != 'disabled' else None)
        control.bind('<Leave>', lambda _e: control.configure(bg=control.normal_bg))
        control.bind('<FocusIn>', lambda _e: control.configure(highlightbackground=c['focus']))
        control.bind('<FocusOut>', lambda _e: control.configure(highlightbackground=c[bg]))
        return control

    def card(self, parent, title=None, subtitle=None, actions=None):
        """A surface with an optional title row. Returns (outer, body)."""
        c = self.c
        outer = tk.Frame(parent, bg=c['surface'], highlightthickness=1, highlightbackground=c['border'],
                         padx=SPACE['lg'], pady=SPACE['md'])
        if title:
            head = tk.Frame(outer, bg=c['surface'])
            head.pack(fill='x', pady=(0, SPACE['sm']))
            self.label(head, title, 'heading', bg='surface').pack(side='left')
            if actions:
                for text, command, kind in reversed(actions):
                    self.button(head, text, command, kind, small=True).pack(side='right', padx=(SPACE['xs'], 0))
            if subtitle:
                fit_wrap(self.label(outer, subtitle, 'small', 'muted', bg='surface')).pack(fill='x', pady=(0, SPACE['sm']))
        body = tk.Frame(outer, bg=c['surface'])
        body.pack(fill='both', expand=True)
        return outer, body

    def section(self, parent, text, bg='surface'):
        tk.Label(parent, text=text.upper(), font=self.fonts['small'], fg=self.c['subtle'], bg=self.c[bg],
                 anchor='w').pack(fill='x', pady=(SPACE['md'], SPACE['xs']))

    def pill(self, parent, variable, bg='surface', role='small'):
        """Status with a symbol, colour and the words themselves; follows a StringVar."""
        holder = tk.Frame(parent, bg=self.c[bg])
        dot = tk.Label(holder, font=self.fonts[role], bg=self.c[bg])
        dot.pack(side='left', padx=(0, SPACE['xs']))
        words = tk.Label(holder, textvariable=variable, font=self.fonts[role], fg=self.c['text'], bg=self.c[bg],
                         justify='left', anchor='w')
        words.pack(side='left', fill='x')
        colours = {'ok': 'success', 'busy': 'info', 'warn': 'warning', 'error': 'danger', 'idle': 'subtle'}

        def refresh(*_):
            state = state_of(variable.get())
            dot.configure(text=STATE_SYMBOL[state], fg=self.c[colours[state]])
            holder.state = state
        variable.trace_add('write', refresh)
        refresh()
        holder.dot, holder.words = dot, words
        return holder

    def notice(self, parent, text, kind='info', bg='surface', wrap=560):
        """An inline message with an icon; kind is info, success, warning or error."""
        icon = {'info': 'ⓘ', 'success': '✓', 'warning': '▲', 'error': '■'}[kind]
        colour = {'info': 'info', 'success': 'success', 'warning': 'warning', 'error': 'danger'}[kind]
        row = tk.Frame(parent, bg=self.c[bg])
        tk.Label(row, text=icon, font=self.fonts['small'], fg=self.c[colour], bg=self.c[bg]).pack(side='left', anchor='n',
                                                                                                padx=(0, SPACE['sm']))
        fit_wrap(tk.Label(row, text=text, font=self.fonts['small'], fg=self.c['muted'], bg=self.c[bg], wraplength=wrap,
                          justify='left', anchor='w')).pack(side='left', fill='x', expand=True)
        return row

    def toggle(self, parent, title, variable, help_text=None, bg='surface'):
        """A switch with its label and optional help line. Space/Return toggles; state is shown in words too."""
        c = self.c
        row = tk.Frame(parent, bg=c[bg])
        row.pack(fill='x', pady=SPACE['xs'])
        state = tk.Label(row, font=self.fonts['small'], fg=c['muted'], bg=c[bg], width=3)
        switch = tk.Canvas(row, width=40, height=22, bg=c[bg], highlightthickness=2, highlightbackground=c[bg],
                           highlightcolor=c['focus'], takefocus=True, cursor='hand2')
        switch.pack(side='right')
        state.pack(side='right', padx=(0, SPACE['xs']))
        text = tk.Frame(row, bg=c[bg])
        text.pack(side='left', fill='x', expand=True)
        fit_wrap(tk.Label(text, text=title, font=self.fonts['body'], fg=c['text'], bg=c[bg], anchor='w',
                          justify='left')).pack(fill='x')
        if help_text:
            fit_wrap(tk.Label(text, text=help_text, font=self.fonts['small'], fg=c['muted'], bg=c[bg], anchor='w',
                              justify='left', wraplength=420)).pack(fill='x')

        def draw(*_):
            on = bool(variable.get())
            fill = c['success'] if on else c['raised']
            switch.delete('all')
            switch.create_oval(1, 1, 21, 21, fill=fill, outline=fill)
            switch.create_oval(19, 1, 39, 21, fill=fill, outline=fill)
            switch.create_rectangle(11, 1, 29, 21, fill=fill, outline=fill)
            x = 20 if on else 3
            switch.create_oval(x, 3, x + 17, 20, fill='#FFFFFF', outline=c['border'])
            state.configure(text='On' if on else 'Off')

        def flip(_event=None):
            variable.set(not variable.get())
            return 'break'
        for event in ('<Button-1>', '<space>', '<Return>'):
            switch.bind(event, flip)
        switch.bind('<FocusIn>', lambda _e: switch.configure(highlightbackground=c['focus']))
        switch.bind('<FocusOut>', lambda _e: switch.configure(highlightbackground=c[bg]))
        variable.trace_add('write', draw)
        draw()
        row.switch = switch
        return row

    def entry(self, parent, variable, secret=False, width=None):
        options = dict(textvariable=variable, style='Desk.TEntry', font=self.fonts['body'])
        if secret:
            options['show'] = '•'
        if width:
            options['width'] = width
        return ttk.Entry(parent, **options)

    def field(self, parent, title, variable, help_text=None, secret=False, bg='surface', row=None):
        """A labelled input on a two-column grid: label and help on the left, the input on the right."""
        c = self.c
        cell = tk.Frame(parent, bg=c[bg])
        tk.Label(cell, text=title, font=self.fonts['body'], fg=c['text'], bg=c[bg], anchor='w',
                 justify='left', wraplength=300).pack(fill='x')
        if help_text:
            tk.Label(cell, text=help_text, font=self.fonts['small'], fg=c['muted'], bg=c[bg], anchor='w',
                     justify='left', wraplength=300).pack(fill='x')
        widget = self.entry(parent, variable, secret=secret)
        if row is None:
            cell.pack(fill='x', pady=(SPACE['sm'], 2))
            widget.pack(fill='x')
        else:
            cell.grid(row=row, column=0, sticky='nw', padx=(0, SPACE['lg']), pady=SPACE['sm'])
            widget.grid(row=row, column=1, sticky='ew', pady=SPACE['sm'])
        return widget

    def segmented(self, parent, variable, options, bg='surface'):
        """Mutually exclusive choices as joined buttons; the selected one is filled and marked."""
        c = self.c
        holder = tk.Frame(parent, bg=c['border'], padx=1, pady=1)
        buttons = []
        for text, value in options:
            control = tk.Label(holder, text=text, font=self.fonts['small'], cursor='hand2', takefocus=True,
                               padx=SPACE['md'], pady=SPACE['xs'] + 2, highlightthickness=2,
                               highlightbackground=c['surface_alt'], highlightcolor=c['focus'])
            control.pack(side='left', fill='x', expand=True)
            choose = lambda _e=None, v=value: (variable.set(v), 'break')[1]
            for event in ('<Button-1>', '<space>', '<Return>'):
                control.bind(event, choose)
            control.bind('<FocusIn>', lambda _e, w=control: w.configure(highlightbackground=c['focus']))
            control.bind('<FocusOut>', lambda _e, w=control: w.configure(highlightbackground=w.cget('bg')))
            buttons.append((control, value, text))

        def refresh(*_):
            for control, value, text in buttons:
                chosen = variable.get() == value
                control.configure(text=('✓ ' if chosen else '') + text,
                                  bg=c['accent'] if chosen else c['surface_alt'],
                                  fg=c['on_accent'] if chosen else c['text'],
                                  highlightbackground=c['accent'] if chosen else c['surface_alt'])
        variable.trace_add('write', refresh)
        refresh()
        holder.buttons = buttons
        return holder

    def text_view(self, parent, text='', mono=False, bg='surface', height=None):
        """A read-only, scrollable text area with styling tags for meta lines and headings."""
        c = self.c
        holder = tk.Frame(parent, bg=c[bg])
        box = tk.Text(holder, bg=c['field'], fg=c['text'], wrap='word', relief='flat', bd=0,
                      font=self.fonts['mono' if mono else 'body'], padx=SPACE['md'], pady=SPACE['md'],
                      highlightthickness=1, highlightbackground=c['border'], highlightcolor=c['focus'],
                      selectbackground=c['selection'], insertbackground=c['text'], spacing1=1, spacing3=1)
        if height:
            box.configure(height=height)
        scroll = ttk.Scrollbar(holder, command=box.yview)
        scroll.pack(side='right', fill='y')
        box.configure(yscrollcommand=scroll.set)
        box.pack(side='left', fill='both', expand=True)
        box.tag_configure('heading', font=self.fonts['heading'], foreground=c['text'], spacing3=4)
        box.tag_configure('muted', foreground=c['muted'])
        box.tag_configure('meta', font=self.fonts['small'], foreground=c['muted'], spacing1=8)
        box.tag_configure('mine', foreground=c['info'])
        box.tag_configure('error', foreground=c['danger'])
        box.tag_configure('ok', foreground=c['success'])
        set_text(box, text)
        holder.pack(fill='both', expand=True, pady=(SPACE['sm'], 0))
        box.holder = holder
        return box

    def empty_state(self, parent, title, body, action=None, bg='surface'):
        holder = tk.Frame(parent, bg=self.c[bg])
        self.label(holder, title, 'heading', bg=bg).pack(anchor='w')
        self.label(holder, body, 'small', 'muted', bg=bg, wrap=520).pack(anchor='w', pady=(2, SPACE['sm']))
        if action:
            text, command = action
            self.button(holder, text, command, 'primary', small=True, bg=bg).pack(anchor='w')
        return holder


def fit_wrap(label, limit=640):
    """Wrap a label to the width it is given, so text never runs past a narrow card.

    The label asks for almost no width of its own (width=1), so long text cannot
    push its card wider than the column; pack/grid then stretch it and it wraps.
    """
    label.configure(width=1)

    def resize(event):
        width = max(80, min(limit, event.width - 4))
        if str(label.cget('wraplength')) != str(width):     # cget may return a Tcl object
            label.configure(wraplength=width)
    label.bind('<Configure>', resize)
    return label


META = re.compile(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}')


def set_text(box, text):
    """Replace a read-only Text's content and apply light styling by line shape."""
    box.configure(state='normal')
    box.delete('1.0', 'end')
    box.insert('1.0', text)
    style_lines(box)
    box.configure(state='disabled')


def style_lines(box):
    """Meta lines (timestamps) are small and muted; our own messages are tinted; failures are red."""
    lines = int(box.index('end-1c').split('.')[0])
    for number in range(1, lines + 1):
        line = box.get(f'{number}.0', f'{number}.end')
        if META.match(line):
            box.tag_add('meta', f'{number}.0', f'{number}.end')
            if '  You' in line:
                box.tag_add('mine', f'{number}.0', f'{number}.end')
            if '✗' in line:
                box.tag_add('error', f'{number}.0', f'{number}.end')
