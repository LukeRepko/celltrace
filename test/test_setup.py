# Copyright (c) 2026 Luke Repko
# SPDX-License-Identifier: GPL-3.0-or-later

"""Installer prerequisite failures must happen before installing dependencies."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('setup_celltrace', Path(__file__).resolve().parents[1] / 'tools/setup_celltrace.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class PrerequisiteTests(unittest.TestCase):
    def test_node_engine_boundaries(self):
        for version in ('v20.19.0', 'v22.0.0', 'v22.11.9', 'v23.0.0', 'v25.0.0', 'v24.0.0-rc.1', 'invalid'):
            with self.subTest(version=version):
                self.assertFalse(setup.supported_node(version))
        for version in ('v22.12.0', 'v22.20.1', '24.0.0', 'v24.21.0\n', 'v26.0.0'):
            with self.subTest(version=version):
                self.assertTrue(setup.supported_node(version))

    def test_old_python_fails_before_node_or_installation(self):
        with patch.object(setup.sys, 'version_info', (3, 11)), patch.object(setup, 'find_node') as node:
            with self.assertRaisesRegex(SystemExit, 'Python 3.12'):
                setup.main()
            node.assert_not_called()

    def test_missing_venv_support_is_actionable(self):
        with patch.object(setup.sys, 'version_info', (3, 12)), \
                patch.object(setup.sys, 'platform', 'linux'), \
                patch.object(setup.importlib.util, 'find_spec', return_value=None):
            with self.assertRaisesRegex(SystemExit, 'python3-venv'):
                setup.check_python()

    def test_missing_npm_fails_before_installation(self):
        with patch.object(setup, 'check_python'), patch.object(setup, 'find_node', return_value=Path('/node/bin/node')), \
                patch.object(setup.shutil, 'which', return_value=None), patch.object(setup.subprocess, 'run') as run:
            with self.assertRaisesRegex(SystemExit, 'npm is missing'):
                setup.main()
            run.assert_not_called()

    def test_arm_without_suitable_node_fails_before_download(self):
        with patch.object(setup, 'usable_node', return_value=False), \
                patch.object(setup.platform, 'machine', return_value='aarch64'), \
                patch.object(setup.urllib.request, 'urlopen') as download:
            with self.assertRaisesRegex(SystemExit, 'for your architecture'):
                setup.find_node()
            download.assert_not_called()


if __name__ == '__main__':
    unittest.main()
