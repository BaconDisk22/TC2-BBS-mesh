"""Node lookup skips malformed SDK metadata without losing valid nodes."""
import unittest
from sitdown_utilities import lookup_nodes

class MalformedNodeLookupTests(unittest.TestCase):
    def test_bad_entries_are_skipped(self):
        nodes = {'!00000001': None, '!00000002': 'bad',
                 '!00000003': {'user': []},
                 '!00000004': {'user': {'shortName': 'OK', 'longName': 'Valid'}}}
        rows = lookup_nodes(nodes)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]['shortName'], 'OK')

if __name__ == '__main__':
    unittest.main()
