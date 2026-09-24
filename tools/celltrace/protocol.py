# Copyright (c) 2026 Luke Repko
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared, lossless CSV and legacy/current summary parser."""
import csv
import math
from datetime import datetime

FIELDS = ('test_id,uptime_ms,state,valid,voltage_v,current_a,power_w,elapsed_s,'
          'ah,mah,wh,kwh,remaining_est_pct,rated_ah_pct,rated_wh_pct,reason').split(',')
STATES = {'IDLE', 'ARMED', 'RUNNING', 'FINISHED'}


class Dashboard:
    """Retain raw traffic independently of the parsed dashboard and event list."""
    def __init__(self):
        self.raw = []
        self.events = []
        self.summary = []
        self.collecting_summary = False
        self.sample = None
        self.updated = None
        self.rated_ah = '20'
        self.rated_wh = '256'
        self.csv_seen = False
        self.reset_count = 0
        self.summary_version = 0

    def event(self, message):
        if not self.events or self.events[-1][1] != message:
            self.events.append((datetime.now().strftime('%H:%M:%S'), message))

    def accept(self, line, now):
        self.raw.append(line)
        if line.startswith('# RESET:'):
            self.sample = None
            self.updated = None
            self.csv_seen = False
            self.collecting_summary = False
            self.summary = []
            self.reset_count += 1
            self.event('DEVICE RESET - previous device totals lost; no automatic resume')
        if line == ','.join(FIELDS):
            self.csv_seen = True
            return
        try:
            row = next(csv.reader([line]))
        except csv.Error:
            self.event('Malformed serial line ignored; retained in raw output/log')
            return
        if len(row) == len(FIELDS) and row[2] in STATES:
            values = dict(zip(FIELDS, row))
            try:
                for name in ('test_id', 'uptime_ms'):
                    if int(values[name]) < 0:
                        raise ValueError('negative counter')
                if values['valid'] not in ('0', '1'):
                    raise ValueError('invalid validity flag')
                for name in FIELDS[4:15]:
                    if not values[name]:
                        if (name in FIELDS[4:7] and values['valid'] == '0') or name in FIELDS[12:15]:
                            continue
                        raise ValueError('missing value')
                    if not math.isfinite(float(values[name])):
                        raise ValueError('non-finite value')
                if float(values['elapsed_s']) < 0:
                    raise ValueError('negative elapsed time')
            except ValueError:
                self.event('Malformed measurement ignored; inspect F2 raw output')
                return
            previous = self.sample
            if previous and previous['test_id'] != values['test_id'] and values['state'] != 'FINISHED':
                self.summary = []
            if not previous or (previous['test_id'], previous['state']) != (values['test_id'], values['state']):
                self.event(f"Test #{values['test_id']}: {values['state']}")
            if values['reason'] != 'none' and (not previous or previous['reason'] != values['reason']):
                self.event('End reason: ' + values['reason'])
            if previous and previous['valid'] != values['valid']:
                self.event('Readings restored' if values['valid'] == '1' else 'Sensor readings unavailable')
            self.sample = values
            self.updated = now
            self.csv_seen = True
            return
        if line.startswith('# Rated capacity Ah: '):
            self.rated_ah = line.split(': ', 1)[1]
        if line.startswith('# Rated energy Wh: '):
            self.rated_wh = line.split(': ', 1)[1]
        if 'CAPACITY TEST SUMMARY' in line:
            self.summary = []
            self.collecting_summary = True
        if self.collecting_summary:
            self.summary.append(line.removeprefix('# '))
            if line.startswith('# Result is frozen'):
                self.collecting_summary = False
                self.summary_version += 1
                self.event('Final summary received - F3 to view all fields')
            return
        # Periodic human-mode measurements remain available verbatim in raw view.
        recurring = ('# CELLTRACE #', '# GOLDENMATE #', '# Live:', '# Discharged:', '# Energy:',
                     '# Remaining estimate:', '# Rated delivered:')
        if line and not line.startswith(recurring):
            self.event(line.removeprefix('# '))
