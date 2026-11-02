"""Build version stamping for the source module and the Mac app's Info.plist."""
import importlib.util
import plistlib
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('stamp_version', ROOT / 'scripts' / 'stamp_version.py')
stamp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stamp)


class VersionStampTests(unittest.TestCase):
    def test_repo_default_is_dev(self):
        import sitdown_version
        self.assertEqual(sitdown_version.VERSION, 'dev')

    def test_source_module_round_trips(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'sitdown_version.py'
            stamp.write_source('v0.1.0-rc.7', path)
            namespace = {}
            exec(path.read_text(), namespace)
            self.assertEqual(namespace['VERSION'], 'v0.1.0-rc.7')

    def test_plist_versions_set_and_other_keys_kept(self):
        with tempfile.TemporaryDirectory() as d:
            contents = Path(d) / 'X.app' / 'Contents'
            contents.mkdir(parents=True)
            with (contents / 'Info.plist').open('wb') as f:
                plistlib.dump({'CFBundleName': 'The Sitdown BBS', 'CFBundleShortVersionString': '0.0.0'}, f)
            stamp.write_plist(contents.parent, 'test-abc1234')
            with (contents / 'Info.plist').open('rb') as f:
                info = plistlib.load(f)
            self.assertEqual((info['CFBundleShortVersionString'], info['CFBundleVersion'], info['CFBundleName']),
                             ('test-abc1234', 'test-abc1234', 'The Sitdown BBS'))

    def test_unsafe_versions_are_refused(self):
        for bad in ('', 'v1 2', "v1'; rm", 'x' * 50, '-v1'):
            with self.subTest(bad=bad), self.assertRaises(SystemExit):
                stamp.check(bad)


if __name__ == '__main__':
    unittest.main()
