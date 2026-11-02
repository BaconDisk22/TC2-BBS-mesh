import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from google.protobuf.struct_pb2 import Struct
from sitdown_control import emit, PREFIX


class ProtobufSnapshotTests(unittest.TestCase):
    def test_nested_protobuf_position_is_serializable(self):
        position = Struct()
        position.update({'latitude': 41.34, 'longitude': -73.07, 'altitude': 42})
        out = io.StringIO()
        with patch.dict(os.environ, {'SITDOWN_CONTROL_TOKEN': 'test-token'}):
            with redirect_stdout(out):
                emit({'type': 'snapshot', 'nodes': [{'id': '!12345678', 'position': position}]})
        line = out.getvalue().strip()
        self.assertTrue(line.startswith(PREFIX + 'test-token '))
        payload = json.loads(line[len(PREFIX + 'test-token '):])
        self.assertAlmostEqual(payload['nodes'][0]['position']['latitude'], 41.34)

    def test_unsupported_object_fails_explicitly(self):
        with self.assertRaises(TypeError):
            with redirect_stdout(io.StringIO()):
                emit({'unexpected': object()})


if __name__ == '__main__':
    unittest.main()
