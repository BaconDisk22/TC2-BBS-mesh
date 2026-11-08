"""The release workflow builds both Mac architectures and publishes them together."""
import unittest
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parent.parent / '.github' / 'workflows' / 'publish-intel-mac-release.yml'


class ReleaseWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text()

    def test_matrix_covers_intel_and_apple_silicon(self):
        for fragment in ('runner: macos-15-intel', 'cpu: x86_64', 'slug: Intel-Mac',
                         'runner: macos-15\n', 'cpu: arm64', 'slug: Apple-Silicon',
                         'test "$(uname -m)" = "${{ matrix.cpu }}"'):
            self.assertIn(fragment, self.text)

    def test_publish_job_ships_both_dmgs_with_combined_checksums(self):
        self.assertIn('needs: mac-release', self.text)
        self.assertIn('cat SHA256SUMS-*.txt > SHA256SUMS.txt', self.text)
        self.assertIn('sha256sum -c SHA256SUMS.txt', self.text)
        self.assertEqual(self.text.count('"The-Sitdown-BBS-Apple-Silicon-$VERSION.dmg"'), 2)

    def test_releases_repo_publish_is_optional_and_token_scoped(self):
        self.assertIn('GH_TOKEN: ${{ secrets.RELEASES_TOKEN }}', self.text)
        self.assertIn('if [[ -z "$GH_TOKEN" ]]', self.text)
        self.assertIn('TheLogsAreScreaming/The-Sitdown-BBS-Releases', self.text)
        self.assertNotIn('RELEASES_TOKEN }}\n          VERSION: ${{ steps', self.text)   # only in the publish job


if __name__ == '__main__':
    unittest.main()
