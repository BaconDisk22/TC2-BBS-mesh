"""Meshtastic channel encryption for MQTT packets, using operator-supplied keys only.

A channel key (PSK) is configured per network as `Name=base64key` pairs. With a
key, broker packets on that channel can be read (and, when the operator opts in,
sent). Without one, nothing is decrypted. Keys are never logged or displayed.

Meshtastic uses AES-CTR with a 16-byte (AES-128) or 32-byte (AES-256) key and a
nonce of packet id (uint64 LE) + sender node (uint32 LE) + four zero bytes.
"""
import base64
import binascii
import struct

DEFAULT_KEY = bytes.fromhex('d4f1bb3a20290759f0bcffabcf4e6901')
MAX_KEYS = 8


def parse_psk(text):
    """Key bytes for one base64 PSK; the Meshtastic 1-byte shorthands map to the default key."""
    try:
        raw = base64.b64decode(str(text).strip(), validate=True)
    except (binascii.Error, ValueError):
        raise ValueError('Channel key is not valid base64.') from None
    if len(raw) == 1:
        if raw[0] == 0:
            raise ValueError('This channel has encryption turned off; there is nothing to decrypt.')
        if raw[0] > 10:
            raise ValueError('Unknown 1-byte channel key.')
        return DEFAULT_KEY[:-1] + bytes([(DEFAULT_KEY[-1] + raw[0] - 1) & 0xff])
    if len(raw) not in (16, 32):
        raise ValueError('Channel key must be 16 or 32 bytes (AES-128 or AES-256).')
    return raw


def parse_channel_keys(setting):
    """{channel name: key bytes} from 'Name=PSK, Name2=PSK2'. Raises ValueError naming the bad entry."""
    keys = {}
    for entry in (part.strip() for part in (setting or '').split(',')):
        if not entry:
            continue
        name, sep, psk = entry.partition('=')
        name = name.strip()
        # base64 may end in '='; partition splits on the first one, which follows the name.
        if not sep or not name or any(ch.isspace() or ch in '/+#' for ch in name) or len(name) > 11:
            raise ValueError(f'Channel key entry "{name or entry[:12]}" must look like Name=base64key '
                             '(name up to 11 characters, no spaces).')
        if name in keys:
            raise ValueError(f'Channel "{name}" has two keys.')
        try:
            keys[name] = parse_psk(psk)
        except ValueError as exc:
            raise ValueError(f'Channel "{name}": {exc}') from None
    if len(keys) > MAX_KEYS:
        raise ValueError(f'At most {MAX_KEYS} channel keys per network.')
    return keys


def safe_channel_keys(setting):
    """Like parse_channel_keys, but an invalid setting simply reads nothing."""
    try:
        return parse_channel_keys(setting)
    except ValueError:
        return {}


def nonce(packet_id, sender):
    return struct.pack('<QI', packet_id & 0xFFFFFFFFFFFFFFFF, sender & 0xFFFFFFFF) + b'\0' * 4


def crypt(key, packet_id, sender, data):
    """AES-CTR in either direction (encrypt and decrypt are the same operation)."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    worker = Cipher(algorithms.AES(key), modes.CTR(nonce(packet_id, sender))).encryptor()
    return worker.update(bytes(data)) + worker.finalize()


def channel_hash(name, key):
    """The 1-byte channel number Meshtastic puts on encrypted packets."""
    value = 0
    for byte in name.encode('utf-8') + key:
        value ^= byte
    return value


def open_packet(key, packet_id, sender, ciphertext):
    """The decoded Data protobuf, or None when this key does not open the packet."""
    from meshtastic.protobuf import mesh_pb2
    if not ciphertext or len(ciphertext) > 512:
        return None
    data = mesh_pb2.Data()
    try:
        data.ParseFromString(crypt(key, packet_id, sender, ciphertext))
    except Exception:
        return None
    # A wrong key usually still "parses"; a real packet always names a known port.
    if data.portnum == 0:
        return None
    try:
        from meshtastic.protobuf import portnums_pb2
        portnums_pb2.PortNum.Name(data.portnum)
    except ValueError:
        return None
    return data
