"""First-launch help ships in every DMG and the checker is valid bash with the documented exit codes."""
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ('intel-mac-test-build.yml', 'apple-silicon-mac-test-build.yml', 'publish-intel-mac-release.yml')


class FirstLaunchHelpTests(unittest.TestCase):
    def test_read_me_first_covers_the_essentials(self):
        text = (ROOT / 'packaging' / 'READ ME FIRST.txt').read_text()
        for phrase in ('Applications', 'Open Anyway', 'Privacy & Security', 'several minutes',
                       'Contents/MacOS', 'check-mac-launch.sh', 'spctl --master-disable'):
            self.assertIn(phrase, text)

    def test_checker_is_valid_bash_and_read_only(self):
        script = (ROOT / 'scripts' / 'check-mac-launch.sh').read_text()
        if shutil.which('bash'):
            subprocess.run(['bash', '-n', str(ROOT / 'scripts' / 'check-mac-launch.sh')], check=True)
        for forbidden in ('xattr -d', 'xattr -c', 'xattr -w', 'spctl --master', 'spctl --add', 'sudo', 'rm '):
            self.assertNotIn(forbidden, script)
        self.assertEqual(sorted(set(re.findall(r'exit (\d)', script))), ['0', '1', '2', '3', '4'])

    def test_missing_app_is_reported_without_macos_tools(self):
        if not shutil.which('bash'):
            self.skipTest('bash unavailable')
        result = subprocess.run(['bash', str(ROOT / 'scripts' / 'check-mac-launch.sh'), '/nonexistent/X.app'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 4)
        self.assertIn('NOT FOUND', result.stdout)

    def test_every_mac_workflow_ships_and_checks_the_help(self):
        for name in WORKFLOWS:
            with self.subTest(workflow=name):
                text = (ROOT / '.github' / 'workflows' / name).read_text()
                self.assertIn('cp "packaging/READ ME FIRST.txt" dmg-staging/', text)
                self.assertIn('cp scripts/check-mac-launch.sh dmg-staging/', text)
                self.assertIn('Check the first-launch helper against the built app', text)
                self.assertIn('Verify DMG includes first-launch help', text)
                for code in ('expect 0', 'expect 1', 'expect 2', 'expect 4'):
                    self.assertIn(code, text)


if __name__ == '__main__':
    unittest.main()
