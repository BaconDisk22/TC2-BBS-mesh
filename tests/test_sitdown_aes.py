"""The built-in AES used for Meshtastic channel encryption."""
import unittest

from sitdown_aes import SBOX, ctr, encrypt_block, expand_key


class AESTests(unittest.TestCase):
    def test_fips_197_known_answers(self):
        plain = bytes.fromhex('00112233445566778899aabbccddeeff')
        self.assertEqual(encrypt_block(expand_key(bytes(range(16))), plain).hex(),
                         '69c4e0d86a7b0430d8cdb78070b4c55a')            # FIPS-197 C.1, AES-128
        self.assertEqual(encrypt_block(expand_key(bytes(range(32))), plain).hex(),
                         '8ea2b7ca516745bfeafc49904b496089')            # FIPS-197 C.3, AES-256
        self.assertEqual((SBOX[0x00], SBOX[0x53], SBOX[0xff]), (0x63, 0xed, 0x16))

    def test_sp800_38a_ctr_vector(self):
        key = bytes.fromhex('2b7e151628aed2a6abf7158809cf4f3c')
        counter = bytes.fromhex('f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff')
        plain = bytes.fromhex('6bc1bee22e409f96e93d7e117393172aae2d8a571e03ac9c9eb76fac45af8e51')
        self.assertEqual(ctr(key, counter, plain).hex(),
                         '874d6191b620e3261bef6864990db6ce9806f66b7970fdff8617187bb9fffdff')   # F.5.1

    def test_ctr_is_its_own_inverse_and_handles_partial_blocks_and_wrap(self):
        key = bytes(range(32))
        for size in (0, 1, 15, 16, 17, 100):
            data = bytes(range(size))
            self.assertEqual(ctr(key, b'\xff' * 16, ctr(key, b'\xff' * 16, data)), data)

    def test_rejects_other_key_sizes(self):
        with self.assertRaises(ValueError):
            expand_key(bytes(24))


if __name__ == '__main__':
    unittest.main()
