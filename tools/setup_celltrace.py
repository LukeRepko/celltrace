#!/usr/bin/env python3
# Copyright (c) 2026 Luke Repko
# SPDX-License-Identifier: GPL-3.0-or-later

"""Check prerequisites, install isolated dependencies, and build the dashboard."""
import hashlib
import importlib.util
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
NODE_VERSION = 'v24.21.0'


def check_python():
    if sys.version_info < (3, 12):
        raise SystemExit('CellTrace needs Python 3.12 or newer. Run setup with that Python.')
    if sys.platform != 'linux':
        raise SystemExit('This installer supports Linux. Other host platforms are not yet validated.')
    for module in ('venv', 'ensurepip'):
        if importlib.util.find_spec(module) is None:
            raise SystemExit('Python virtual-environment support is missing. Install the venv package '
                             'for this Python (e.g. python3-venv on Debian/Ubuntu), then rerun setup.')


def supported_node(version):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', version.strip())
    if not match:
        return False
    major, minor, patch = map(int, match.groups())
    # Intersection of the locked Vite and Vitest engine requirements.
    return (major == 22 and (minor, patch) >= (12, 0)) or major == 24 or major >= 26


def usable_node(path):
    if not path:
        return False
    try:
        version = subprocess.check_output([str(path), '--version'], text=True, timeout=10)
        return supported_node(version)
    except (OSError, subprocess.SubprocessError):
        return False


def find_node():
    node = shutil.which('node')
    if usable_node(node):
        return Path(node)
    cache = Path.home() / '.local/share/celltrace'
    cached = cache / 'node/bin/node'
    if usable_node(cached):
        return cached
    if platform.machine() != 'x86_64':
        raise SystemExit('Install Node.js 24 LTS with npm for your architecture, then rerun setup.')

    cache.mkdir(parents=True, exist_ok=True)
    name = f'node-{NODE_VERSION}-linux-x64.tar.xz'
    base = f'https://nodejs.org/dist/{NODE_VERSION}/'
    with urllib.request.urlopen(base + name, timeout=60) as response:
        data = response.read()
    with urllib.request.urlopen(base + 'SHASUMS256.txt', timeout=30) as response:
        sums = response.read().decode()
    if not any(line.split() == [hashlib.sha256(data).hexdigest(), name] for line in sums.splitlines()):
        raise SystemExit('Node archive checksum mismatch')
    archive = cache / name
    archive.write_bytes(data)
    with tarfile.open(archive) as tf:
        tf.extractall(cache, filter='data')
    link = cache / 'node'
    if link.is_symlink():
        link.unlink()
    elif link.exists():
        raise SystemExit(f'{link} is not a symlink. Install Node.js 24 LTS on PATH or move that directory.')
    link.symlink_to(cache / f'node-{NODE_VERSION}-linux-x64', target_is_directory=True)
    if not usable_node(cached):
        raise SystemExit('The downloaded Node runtime could not start. Install Node.js 24 LTS and rerun setup.')
    return cached


def main():
    check_python()
    node = find_node()
    env = dict(os.environ, PATH=str(node.parent) + os.pathsep + os.environ.get('PATH', ''))
    npm = shutil.which('npm', path=env['PATH'])
    if not npm:
        raise SystemExit('npm is missing. Install Node.js 24 LTS with npm, then rerun setup.')
    try:
        subprocess.run([npm, '--version'], env=env, check=True, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SystemExit('npm could not start. Check your Node.js/npm installation.') from exc

    import venv
    venv.EnvBuilder(with_pip=True).create(ROOT / '.venv')
    python = ROOT / '.venv/bin/python'
    subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(ROOT / 'tools/requirements.lock.txt')], check=True)
    subprocess.run([npm, 'ci'], cwd=ROOT / 'web', env=env, check=True)
    subprocess.run([npm, 'run', 'build'], cwd=ROOT / 'web', env=env, check=True)
    print('Ready. Run ./celltrace (or ./celltrace --demo). No firmware was uploaded.')


if __name__ == '__main__':
    main()
