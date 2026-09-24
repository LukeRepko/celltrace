# Copyright (c) 2026 Luke Repko
# SPDX-License-Identifier: GPL-3.0-or-later

"""Own an ephemeral test server; never attach browser checks to a user's recorder."""
from contextlib import contextmanager
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def isolated_dashboard():
    if not (ROOT / 'web/dist/index.html').is_file():
        raise RuntimeError('Build the dashboard first: run npm run build in web/.')
    with tempfile.TemporaryDirectory(prefix='celltrace-browser-') as directory:
        artifacts = Path(directory)
        with socket.socket() as listener, (artifacts / 'server.log').open('w+') as log:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            url = f'http://127.0.0.1:{listener.getsockname()[1]}'
            process = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), '--serve',
                 str(listener.fileno()), str(artifacts / 'data')],
                pass_fds=(listener.fileno(),), stdout=log, stderr=log, cwd=ROOT)
            listener.close()
            try:
                deadline = time.monotonic() + 15
                # Bypass any host proxy settings for this exclusively local server.
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                while time.monotonic() < deadline and process.poll() is None:
                    try:
                        with opener.open(url + '/api/state', timeout=0.5) as response:
                            if response.status == 200:
                                break
                    except (OSError, urllib.error.URLError):
                        time.sleep(0.1)
                else:
                    log.seek(0)
                    raise RuntimeError('Isolated browser server failed to start:\n' + log.read())
                yield url, artifacts
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def serve(fd, directory):
    sys.path.insert(0, str(ROOT / 'tools'))
    from unittest.mock import patch
    import serial
    import uvicorn
    from celltrace.server import create_app
    from celltrace.service import Recorder

    # Even a mistaken UI action cannot open real serial hardware in this child.
    with patch('serial.Serial', side_effect=serial.SerialException('Physical serial disabled in browser tests')), \
            patch('serial.tools.list_ports.comports', return_value=[]):
        uvicorn.run(create_app(Recorder(directory)), fd=fd, log_level='warning')


if __name__ == '__main__':
    if len(sys.argv) != 4 or sys.argv[1] != '--serve':
        raise SystemExit('This helper is launched by the browser tests.')
    serve(int(sys.argv[2]), sys.argv[3])
