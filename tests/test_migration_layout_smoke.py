"""
Smoke tests for post-migration project layout.

Goal:
1. Ensure moved files are present in their new locations.
2. Ensure critical scripts/config paths point to the new layout.
3. Catch common regressions after workspace directory renames.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestMigrationLayoutSmoke(unittest.TestCase):
    def test_config_files_exist_in_config_dir(self) -> None:
        self.assertTrue((PROJECT_ROOT / "config" / "settings.json").exists())
        self.assertTrue((PROJECT_ROOT / "config" / "accounts.json").exists())

    def test_legacy_root_level_configs_not_present(self) -> None:
        self.assertFalse((PROJECT_ROOT / "settings.json").exists())
        self.assertFalse((PROJECT_ROOT / "accounts.json").exists())

    def test_backend_config_prefers_config_directory(self) -> None:
        src = read_text(PROJECT_ROOT / "backend" / "config.py")
        self.assertIn('os.path.join(BASE_DIR, "config", filename)', src)
        self.assertIn('ACCOUNTS_PATH = _resolve_config_path("accounts.json")', src)
        self.assertIn('SETTINGS_PATH = _resolve_config_path("settings.json")', src)

    def test_backend_api_has_current_core_routes(self) -> None:
        src = read_text(PROJECT_ROOT / "backend" / "api.py")
        expected_routes = [
            '"/api/emulators"',
            '"/api/start"',
            '"/api/start/{instance_index}"',
            '"/api/stop"',
            '"/api/status"',
            '"/api/settings"',
            '"/api/accounts"',
            '"/api/screenshot/{instance_index}"',
            '"/api/health"',
            '"/ws"',
        ]
        for route in expected_routes:
            self.assertIn(route, src, msg=f"missing route declaration: {route}")

    def test_windows_agent_scripts_use_new_locations(self) -> None:
        start_bat = read_text(PROJECT_ROOT / "deploy" / "windows" / "start-remote-agent.bat")
        start_vbs = read_text(PROJECT_ROOT / "deploy" / "windows" / "agent-start.vbs")
        self.assertIn(r"python agents\remote_agent.py", start_bat)
        self.assertRegex(start_vbs, re.compile(r'agents\\remote_agent\.py', re.IGNORECASE))

    def test_windows_setup_docs_point_to_config_directory(self) -> None:
        setup_bat = read_text(PROJECT_ROOT / "deploy" / "windows" / "first-setup.bat")
        self.assertIn(r"config\settings.json", setup_bat)
        self.assertIn(r"config\accounts.json", setup_bat)

    def test_remote_agent_and_core_entrypoints_exist(self) -> None:
        self.assertTrue((PROJECT_ROOT / "agents" / "remote_agent.py").exists())
        self.assertTrue((PROJECT_ROOT / "backend" / "main.py").exists())
        self.assertTrue((PROJECT_ROOT / "web" / "package.json").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
