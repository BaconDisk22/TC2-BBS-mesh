#!/usr/bin/env python3
"""Capture the real Tk app in safe demo mode on a macOS GUI runner.

Saves docs/images/desktop-preview-demo.png (README) plus one screenshot per
page in the dark and light themes (<theme>-<page>-preview-demo.png). Settings
and history are isolated in a temporary folder, so this can never connect a
radio or a broker.
"""
import sys
import tempfile
import time
from pathlib import Path
import tkinter as tk
from PIL import ImageGrab

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sitdown_desktop
from sitdown_desktop import SitdownApp

scratch = tempfile.TemporaryDirectory()
sitdown_desktop.CONFIG = Path(scratch.name) / 'launcher.json'
sitdown_desktop.DATA = Path(scratch.name)
root = tk.Tk()
root.geometry('1280x860')
app = SitdownApp(root)
app.demo.set(True)
app.start()
for line in ('DEMONSTRATION DATA — no radio connected', 'The Social Club: example bulletin board',
             'A Word With You: sample mail', 'The Backroom: example activity; no radio traffic transmitted'):
    app.log(line)
now = int(time.time())
app.snapshot['channels'] = [{'index': 0, 'name': 'Primary'}, {'index': 1, 'name': 'CentralNJ'}]
app.snapshot['nodes'].append({'id': '!b2c649cb', 'shortName': 'EDC', 'longName': 'Every Day Carry',
                              'hardware': 'RAK4631', 'role': 'CLIENT', 'lastHeard': now - 90, 'snr': 6.5,
                              'hopsAway': 1, 'deviceMetrics': {'batteryLevel': 87}, 'position': {}})
for text, mine, status in (('Anyone on CentralNJ tonight?', False, None), ('Ayyy, right here.', True, 'relayed'),
                           ('Sending a test from the shore.', False, None)):
    app.chat_log.add({'type': 'chat', 'channel': 1, 'from': '!934cca70' if mine else '!b2c649cb',
                      'short': '' if mine else 'EDC', 'long': '' if mine else 'Every Day Carry', 'text': text,
                      'rx_time': now - 300, 'mine': mine, 'id': 9 if mine else None, 'status': status or 'sending'})
app.chat_log.add({'type': 'dm', 'peer': '!b2c649cb', 'from': '!b2c649cb', 'short': 'EDC', 'long': 'Every Day Carry',
                  'text': 'Got your message, thanks!', 'rx_time': now - 60, 'mine': False})


def settle(delay=500):
    root.update_idletasks()
    root.lift()
    root.update()
    root.after(delay, root.quit)
    root.mainloop()
    root.update()


def grab(destination):
    x, y = root.winfo_rootx(), root.winfo_rooty()
    w, h = root.winfo_width(), root.winfo_height()
    if w < 700 or h < 500:
        raise RuntimeError(f'Unexpected window size: {w}x{h}')
    image = ImageGrab.grab(bbox=(x, y, x + w, y + h))
    if image.width < 700:
        raise RuntimeError('Screenshot capture failed')
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination)
    print(f'Saved {destination}')


images = Path('docs/images')
root.focus_force()
settle(1800)
grab(images / 'desktop-preview-demo.png')
for theme in ('dark', 'light'):
    app.appearance.set(theme)
    for page in ('HOME', 'CHAT', 'NODES', 'WIRE', 'RADIO', 'MESSAGES', 'BOARDS', 'BBS', 'SETTINGS', 'ABOUT'):
        app.select_page(page)
        if page == 'CHAT' and app.chat_tabs is not None:
            app.chat_tabs.select(app.chat_tab_frames[1])
        settle(400)
        grab(images / f'{theme}-{page.lower()}-preview-demo.png')
app.appearance.set('dark')
root.destroy()
scratch.cleanup()
