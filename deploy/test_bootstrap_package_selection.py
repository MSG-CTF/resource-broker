import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


class PackageSelectionTests(unittest.TestCase):
    def test_version_selection_drains_producer_under_pipefail(self):
        script = Path(__file__).parent / "bootstrap" / "node-agent-bootstrap.sh"
        source = script.read_text()
        selection = re.search(r'package_version="\$\((apt-cache madison runsc .*?)\)"', source, re.S)
        self.assertIsNotNone(selection)
        with tempfile.TemporaryDirectory() as temporary:
            producer = Path(temporary) / "apt-cache"
            producer.write_text("#!/usr/bin/env python3\nimport sys\nfor i in range(40000):\n print('runsc | 20261005.0-1 | repository-' + str(i))\n")
            producer.chmod(0o755)
            completed = subprocess.run(
                ["bash", "-o", "pipefail", "-c", 'GVISOR_RELEASE=20261005.0; ' + selection[0] + '; printf "%s\\n" "$package_version"'],
                env={**os.environ, "PATH": temporary + os.pathsep + os.environ["PATH"]},
                capture_output=True, text=True, timeout=15,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "20261005.0-1\n")


if __name__ == "__main__":
    unittest.main()
