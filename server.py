#!/usr/bin/env python3

"""
TC²-BBS Server for Meshtastic by TheCommsChannel (TC²)
Date: 07/14/2024
Version: 0.1.6

Description:
The system allows for mail message handling, bulletin boards, and a channel
directory. It uses a configuration file for setup details and an SQLite3
database for data storage. Mail messages and bulletins are synced with
other BBS servers listed in the config.ini file.
"""

import logging
import time
import queue
import signal
import threading
from pathlib import Path
from sitdown_control import start_reader, snapshot, handle, emit
from sitdown_chat import chat_event

from config_init import initialize_config, get_interface, init_cli_parser, merge_config
from db_operations import initialize_database
from js8call_integration import JS8CallClient
from message_processing import on_receive
from pubsub import pub
from sitdown_utilities import LocalUtilities
from sitdown_link import RadioLinkMonitor

# General logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# JS8Call logging
js8call_logger = logging.getLogger('js8call')
js8call_logger.setLevel(logging.DEBUG)
js8call_handler = logging.StreamHandler()
js8call_handler.setLevel(logging.DEBUG)
js8call_formatter = logging.Formatter('%(asctime)s - JS8Call - %(levelname)s - %(message)s', '%Y-%m-%d %H:%M:%S')
js8call_handler.setFormatter(js8call_formatter)
js8call_logger.addHandler(js8call_handler)

def display_banner():
    """Show Sitdown branding without altering upstream attribution."""
    print("THE SITDOWN BBS")
    print("North Jersey Communications Division")
    print("Meshtastic bulletin board server")


def main():
    display_banner()
    args = init_cli_parser()
    config_file = None
    if args.config is not None:
        config_file = args.config
    system_config = initialize_config(config_file)

    merge_config(system_config, args)

    interface = get_interface(system_config)
    interface.bbs_nodes = system_config['bbs_nodes']
    interface.allowed_nodes = system_config['allowed_nodes']
    interface.sitdown = LocalUtilities(interface, system_config['config'],
        state_path=Path(config_file or 'config.ini').resolve().with_name('sitdown-broadcasts.db'))

    logging.info(f"THE SITDOWN BBS is running on {system_config['interface_type']} interface...")

    initialize_database()

    def receive_packet(packet, interface):
        on_receive(packet, interface)
        if args.desktop_control:
            event = chat_event(packet, interface)
            if event is not None:
                emit(event)

    pub.subscribe(receive_packet, system_config['mqtt_topic'])

    # Initialize and start JS8Call Client if configured
    js8call_client = JS8CallClient(interface)
    js8call_client.logger = js8call_logger

    if js8call_client.db_conn:
        js8call_client.connect()

    requests = start_reader() if args.desktop_control else None
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    interface.sitdown.mqtt.start()
    interface.sitdown.startup()
    link = RadioLinkMonitor(interface)

    try:
        last_snapshot = 0
        while not stopping.wait(0.25):
            if link.check():
                logging.error('Radio session disconnected for 30s; exiting for supervisor recovery')
                return 1
            interface.sitdown.tick()
            if requests is not None:
                try:
                    emit(handle(interface.sitdown, requests.get_nowait()))
                except queue.Empty:
                    pass
                if time.monotonic() - last_snapshot >= 5:
                    emit(snapshot(interface.sitdown))
                    last_snapshot = time.monotonic()

    except KeyboardInterrupt:
        logging.info("Shutting down the server...")
    finally:
        pub.unsubscribe(receive_packet, system_config['mqtt_topic'])
        interface.sitdown.mqtt.stop()
        interface.close()
        if js8call_client.connected:
            js8call_client.close()

if __name__ == "__main__":
    raise SystemExit(main())
