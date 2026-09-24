# Copyright (c) 2026 Luke Repko
# SPDX-License-Identifier: GPL-3.0-or-later

"""Dashboard checks with a simulated screen and port; never opens hardware."""
import curses
from collections import deque
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import serial_console as app


def row(state='RUNNING', valid='1', reason='none'):
    return f'1,1000,{state},{valid},' + ('13.3487,6.4200,85.6987,' if valid == '1' else ',,,') + \
        f'5400.000,9.600000,9600.000,124.000000,0.12400000,52.00,48.00,48.44,{reason}'


SUMMARY = '\n'.join([
    '# ===== GOLDENMATE CAPACITY TEST SUMMARY =====',
    '# Test number: 1', '# End reason: low_voltage_endpoint',
    '# Result: measured to configured voltage endpoint',
    '# Discharge elapsed: 03:10:00', '# Start loaded voltage V: 13.350',
    '# Last loaded voltage V: 9.990', '# Minimum loaded voltage V: 9.990',
    '# Last valid voltage V: 9.990', '# Last loaded current A: 4.995',
    '# Last valid current A: 4.995', '# Average current A: 6.200',
    '# Peak current A: 7.300', '# Discharged Ah: 19.6333',
    '# Discharged mAh: 19633.3', '# Delivered Wh: 249.0000',
    '# Delivered kWh: 0.249000', '# Rated Ah delivered %: 98.17',
    '# Rated Wh delivered %: 97.27', '# Valid integrated samples: 114000',
    '# DISCONNECT LOAD. Software cannot switch off the resistor.',
    '# Result is frozen in RAM until start/reset. Recharge after disconnecting.',
])


class Port:
    port = 'SIMULATED'
    baudrate = 115200

    def __init__(self, chunks):
        self.chunks = deque(chunk.encode() for chunk in chunks)
        self.sent = []

    @property
    def in_waiting(self):
        return len(self.chunks[0]) if self.chunks else 0

    def read(self, size):
        return self.chunks.popleft() if self.chunks else b''

    def write(self, data):
        self.sent.append(data)
        return len(data)


class Screen:
    def __init__(self, keys, clock, height=32, width=110):
        self.keys = deque(keys)
        self.clock = clock
        self.height, self.width = height, width
        self.frames = []
        self.rows = {}

    def timeout(self, value): pass
    def getmaxyx(self): return self.height, self.width
    def erase(self): self.rows = {}
    def addnstr(self, row, col, text, limit, style): self.rows[row] = text[:limit]
    def move(self, row, col):
        assert 0 <= row < self.height and 0 <= col < self.width
    def refresh(self): self.frames.append(dict(self.rows))
    def get_wch(self):
        self.clock[0] += 0.2
        key = self.keys.popleft() if self.keys else '\x03'
        return key(self) if callable(key) else key


def run_ui(chunks, keys, height=32, width=110):
    clock = [0.0]
    port = Port(chunks)
    screen = Screen(keys, clock, height, width)
    log = io.BytesIO()
    with patch.object(app.curses, 'curs_set'), patch.object(app.curses, 'has_colors', return_value=False), \
            patch.object(app.time, 'monotonic', side_effect=lambda: clock[0]):
        app.console(screen, port, log)
    return screen, port, log


class DashboardTests(unittest.TestCase):
    def test_parser_precision_fault_reset_and_summary(self):
        model = app.Dashboard()
        model.accept(row(), 0)
        self.assertEqual(model.sample['wh'], '124.000000')
        model.accept(row(valid='0', reason='sensor_fault_incomplete'), 1)
        self.assertEqual(model.sample['voltage_v'], '')
        model.accept(row().replace('13.3487', 'nan'), 2)
        self.assertEqual(model.updated, 1)
        for line in SUMMARY.splitlines(): model.accept(line, 3)
        self.assertEqual(len(model.summary), len(SUMMARY.splitlines()))
        self.assertFalse(model.collecting_summary)
        model.accept('# RESET: prior results lost', 4)
        self.assertIsNone(model.sample)
        self.assertIn('# Delivered Wh: 249.0000', model.raw)

    def test_fixed_input_and_no_test_commands(self):
        chunks = [row() + '\n'] * 9
        screen, port, log = run_ui(chunks, list('status') + ['\n', '\x03'])
        self.assertEqual(port.sent, [b'status\n'])
        self.assertEqual(screen.frames[6][31], '> status')
        rendered = '\n'.join(screen.frames[6].values())
        self.assertIn('6.4200 A', rendered)
        self.assertIn('124.000000 Wh', rendered)
        self.assertIn('52.00%', rendered)
        self.assertIn('9600.000 mAh', rendered)
        self.assertTrue(log.getvalue().startswith(row().encode()))
        Path('/tmp/battery-dashboard-preview.txt').write_text(rendered)

    def test_summary_scroll_all_fields_and_resize(self):
        completed = row('FINISHED', reason='low_voltage_endpoint') + '\n' + SUMMARY + '\n'
        def resize(screen):
            screen.width = 45
            return curses.KEY_RESIZE
        keys = [curses.KEY_NPAGE] * 12 + [resize] + [curses.KEY_NPAGE] * 20 + ['\x03']
        screen, port, _ = run_ui([completed], keys, height=16, width=80)
        all_text = '\n'.join(text for frame in screen.frames for text in frame.values())
        for field in ['Start loaded voltage', 'Peak current', 'Rated Wh delivered', '114000']:
            self.assertIn(field, all_text)
        self.assertIn('TEST ENDED - DISCONNECT LOAD', all_text)
        self.assertNotIn(b'start\n', port.sent)

    def test_stale_and_missing_data(self):
        def wait(screen):
            screen.clock[0] += 4
            return -1
        screen, _, _ = run_ui([row() + '\n'], [wait, '\x03'])
        text = '\n'.join(screen.frames[-1].values())
        self.assertIn('STALE', text)
        self.assertIn('LAST RECEIVED', text)
        screen, _, _ = run_ui([row(valid='0') + '\n'], ['\x03'])
        self.assertIn('SENSOR READ ERROR', '\n'.join(screen.frames[-1].values()))

    def test_csv_startup_only_and_finished_summary_request(self):
        _, port, _ = run_ui([], [-1] * 18 + ['\x03'])
        self.assertEqual(port.sent, [b'csv\n'])
        _, port, _ = run_ui([row('FINISHED') + '\n'], ['\x03'])
        self.assertEqual(port.sent, [b'summary\n'])


if __name__ == '__main__':
    unittest.main()
