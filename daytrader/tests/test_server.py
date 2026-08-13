from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from daytrader.config import Config
from daytrader.server import Scanner


class ScannerScopeTests(unittest.TestCase):
    def test_daytrader_snapshot_has_no_legacy_quantrex_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            scanner = Scanner(Config(auto_paper=False), Path(directory) / "paper.json")

            self.assertNotIn("quantrex", scanner.snapshot())

    def test_server_cli_does_not_expose_legacy_quantrex_controls(self):
        source = (Path(__file__).parents[1] / "server.py").read_text()

        self.assertNotIn("--quantrex-state-file", source)
        self.assertNotIn("--quantrex-kill-switch", source)


if __name__ == "__main__":
    unittest.main()
