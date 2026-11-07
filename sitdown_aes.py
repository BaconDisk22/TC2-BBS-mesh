"""Small pure-Python AES (FIPS-197) block encryption and CTR mode.

Meshtastic channel packets are at most a few hundred bytes, so a dependency-free
implementation is fast enough and avoids native wheels that are missing for
some Mac architectures. Only the forward cipher is needed: CTR mode encrypts
and decrypts with the same operation.
"""


def _xtime(value):
    value <<= 1
    return (value ^ 0x1B) & 0xFF if value & 0x100 else value


def _build_sbox():
    sbox = [0] * 256
    p = q = 1
    while True:
        # p walks the multiplicative group by 3; q tracks its inverse (by 1/3).
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)
        q ^= q << 1
        q ^= q << 2
        q ^= q << 4
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        rotated = q ^ ((q << 1) | (q >> 7)) ^ ((q << 2) | (q >> 6)) ^ ((q << 3) | (q >> 5)) ^ ((q << 4) | (q >> 4))
        sbox[p] = (rotated ^ 0x63) & 0xFF
        if p == 1:
            break
    sbox[0] = 0x63
    return bytes(sbox)


SBOX = _build_sbox()
RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36)


def expand_key(key):
    """Round keys as a list of 16-byte lists for AES-128 or AES-256."""
    if len(key) not in (16, 32):
        raise ValueError('AES key must be 16 or 32 bytes')
    nk = len(key) // 4
    rounds = nk + 6
    words = [list(key[i:i + 4]) for i in range(0, len(key), 4)]
    for i in range(nk, 4 * (rounds + 1)):
        word = list(words[i - 1])
        if i % nk == 0:
            word = word[1:] + word[:1]
            word = [SBOX[b] for b in word]
            word[0] ^= RCON[i // nk - 1]
        elif nk > 6 and i % nk == 4:
            word = [SBOX[b] for b in word]
        words.append([a ^ b for a, b in zip(words[i - nk], word)])
    return [sum(words[r * 4:r * 4 + 4], []) for r in range(rounds + 1)]


def encrypt_block(round_keys, block):
    state = [b ^ k for b, k in zip(block, round_keys[0])]
    last = len(round_keys) - 1
    for number in range(1, last + 1):
        state = [SBOX[b] for b in state]
        # ShiftRows on a column-major 4x4 state.
        state = [state[(i + 4 * (i % 4)) % 16] for i in range(16)]
        if number != last:
            mixed = []
            for col in range(4):
                a = state[col * 4:col * 4 + 4]
                total = a[0] ^ a[1] ^ a[2] ^ a[3]
                mixed += [a[i] ^ total ^ _xtime(a[i] ^ a[(i + 1) % 4]) for i in range(4)]
            state = mixed
        state = [b ^ k for b, k in zip(state, round_keys[number])]
    return bytes(state)


def ctr(key, counter_block, data):
    """AES-CTR with a 128-bit big-endian counter starting at counter_block."""
    round_keys = expand_key(bytes(key))
    counter = int.from_bytes(counter_block, 'big')
    out = bytearray()
    for offset in range(0, len(data), 16):
        stream = encrypt_block(round_keys, counter.to_bytes(16, 'big'))
        chunk = data[offset:offset + 16]
        out += bytes(a ^ b for a, b in zip(chunk, stream))
        counter = (counter + 1) % (1 << 128)
    return bytes(out)
