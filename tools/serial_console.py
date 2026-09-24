#!/usr/bin/env python3
# Copyright (c) 2026 Luke Repko
# SPDX-License-Identifier: GPL-3.0-or-later

"""Live battery dashboard with fixed command input; requires curses and pyserial."""

import argparse
import codecs
import csv
from contextlib import ExitStack
import curses
from datetime import datetime
import math
import sys
import textwrap
import time

import serial


from celltrace.protocol import Dashboard, FIELDS, STATES


def elapsed(value):
    seconds = max(0, int(float(value)))
    return f'{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}'


def clean(text):
    # Incoming serial escape sequences never control the terminal.
    return ''.join(c if ' ' <= c <= '~' else '    ' if c == '\t' else '?' for c in text)


def console(screen, port, log):
    screen.timeout(50)
    curses.curs_set(1)
    colors = {}
    if curses.has_colors():
        curses.start_color()
        background = curses.COLOR_BLACK
        try:
            curses.use_default_colors()
            background = -1
        except curses.error:
            pass
        for number, (name, foreground) in enumerate((('cyan', curses.COLOR_CYAN),
                ('green', curses.COLOR_GREEN), ('yellow', curses.COLOR_YELLOW),
                ('red', curses.COLOR_RED)), 1):
            curses.init_pair(number, foreground, background)
            colors[name] = curses.color_pair(number)

    model = Dashboard()
    model.event('Connected. Opening the port may reset the Mega. Only CSV mode is requested automatically.')
    partial = ''
    decoder = codecs.getincrementaldecoder('utf-8')('replace')
    command = draft = ''
    cursor = 0
    history = []
    history_index = 0
    connected = True
    view = 'dashboard'
    scroll = 0
    held_lines = None
    csv_wanted = True
    next_csv_request = time.monotonic() + 2
    requested_summary = None
    summary_version = 0

    def send(text):
        nonlocal connected
        try:
            payload = (text + '\n').encode('ascii')
            if port.write(payload) != len(payload):
                raise serial.SerialException('incomplete command write')
        except (serial.SerialException, OSError) as exc:
            connected = False
            model.event(f'Send failed: {exc}; delivery uncertain; command NOT retried')
            return False
        model.event('> ' + text)
        return True

    while True:
        now = time.monotonic()
        if connected:
            try:
                received = port.read(min(port.in_waiting, 8192))
            except (serial.SerialException, OSError) as exc:
                connected = False
                model.event(f'Disconnected: {exc}; no automatic reconnect')
                received = b''
            if received:
                if log:
                    log.write(received)
                    log.flush()
                for char in decoder.decode(received):
                    if char == '\n':
                        reset_before = model.reset_count
                        model.accept(partial.rstrip('\r'), now)
                        if model.reset_count != reset_before:
                            requested_summary = None
                            next_csv_request = now + 2
                        partial = ''
                    else:
                        partial += char
                    if len(partial) >= 8192:
                        model.raw.append(partial)
                        model.event('Overlong serial line retained in raw view')
                        partial = ''
            if csv_wanted and not model.csv_seen and now >= next_csv_request:
                send('csv')
                next_csv_request = now + 3
            sample = model.sample
            if sample and sample['state'] == 'FINISHED':
                key = (model.reset_count, sample['test_id'])
                if not model.summary and requested_summary != key:
                    send('summary')
                    requested_summary = key
            if model.summary and not model.collecting_summary and summary_version != model.summary_version:
                summary_version = model.summary_version
                view = 'summary'
                scroll = 0
                held_lines = None

        height, width = screen.getmaxyx()
        screen.erase()
        content = []
        events_begin = None

        def add(text='', style=0):
            # Logical rows wrap on narrow terminals; no numeric precision is discarded.
            content.extend((part, style) for part in
                           (textwrap.wrap(clean(text), max(1, width - 2),
                                          replace_whitespace=False, drop_whitespace=False) or ['']))

        s = model.sample
        age = now - model.updated if model.updated is not None else None
        state = s['state'] if s else 'WAITING FOR DATA'
        bad = not connected or (age is not None and age > 3) or (s and s['valid'] == '0')
        freshness = ('DISCONNECTED' if not connected else 'WAITING FOR CSV' if age is None else
                     f'STALE ({age:.0f}s)' if age > 3 else
                     'SENSOR READ ERROR' if s['valid'] == '0' else 'LIVE')
        alert = colors.get('red', 0) | curses.A_BOLD
        accent = colors.get('cyan', 0) | curses.A_BOLD
        status_style = colors.get('green' if state == 'RUNNING' else 'yellow', 0) | curses.A_BOLD
        if bad or state == 'FINISHED':
            status_style = alert
        # Persistent header and input remain outside the scrollable content.
        def draw(row, text, style=0):
            try:
                screen.addnstr(row, 0, clean(text), max(0, width - 1), style)
            except curses.error:
                pass

        if height < 10 or width < 30:
            draw(0, 'Enlarge terminal (30x10 minimum).')
            draw(1, 'Ctrl+C exits; device keeps running.')
        else:
            draw(0, ' CELLTRACE  /  ' + view.upper(), curses.A_REVERSE | curses.A_BOLD)
            draw(1, f" #{s['test_id'] if s else '-'}  {state}  |  {freshness}", status_style)
            if state == 'FINISHED':
                draw(2, ' TEST ENDED - DISCONNECT LOAD', alert)
            else:
                draw(2, ' ' + port.port + '  |  ' + str(port.baudrate) + ' baud  |  Manual load disconnect')

            if view != 'raw':
                if s:
                    prefix = 'LAST RECEIVED (not live)' if bad else 'LIVE'
                    if s['valid'] == '1':
                        if width >= 80 and height >= 28:
                            add(prefix + ' READINGS', alert if bad else accent)
                            add(f"{'VOLTAGE':<24}{'CURRENT':<24}POWER", accent)
                            add(f"{s['voltage_v'] + ' V':<24}{s['current_a'] + ' A':<24}{s['power_w']} W", alert if bad else curses.A_BOLD)
                        else:
                            add(f"{prefix}:  {s['voltage_v']} V    {s['current_a']} A    {s['power_w']} W", alert if bad else accent)
                    else:
                        add('LIVE:  -- V    -- A    -- W  /  SENSOR READ ERROR', alert)
                    add(f"Discharge elapsed: {elapsed(s['elapsed_s'])}  ({s['elapsed_s']} s)")
                else:
                    add('Waiting for measurements; requesting CSV mode...', colors.get('yellow', 0))
                if view == 'dashboard' and s:
                    add()
                    if width >= 80:
                        column = (width - 2) // 2
                        add(f"{'DISCHARGED CAPACITY':<{column}}DELIVERED ENERGY", accent)
                        add(f"{s['ah'] + ' Ah':<{column}}{s['wh']} Wh", curses.A_BOLD)
                        add(f"{s['mah'] + ' mAh':<{column}}{s['kwh']} kWh")
                        add(f"{'Rated delivered: ' + s['rated_ah_pct'] + '%':<{column}}Rated delivered: {s['rated_wh_pct']}%")
                    else:
                        add('DISCHARGED CAPACITY / DELIVERED ENERGY', accent)
                        add(f"{s['ah']} Ah / {s['mah']} mAh")
                        add(f"{s['wh']} Wh / {s['kwh']} kWh")
                        add(f"Rated delivered: {s['rated_ah_pct']}% Ah / {s['rated_wh_pct']}% Wh")
                    add()
                    percent = s['remaining_est_pct']
                    if percent:
                        bars = min(32, max(10, width - 32))
                        fill = int(max(0, min(100, float(percent))) * bars / 100)
                        add('ESTIMATED REMAINING  [' + '#' * fill + '-' * (bars - fill) + '] ' + percent + '%', status_style)
                    else:
                        add('ESTIMATED REMAINING  -- (' + ('no test started' if s['state'] == 'IDLE' else 'expected Ah unknown') + ')')
                    add(f'Assumes full at start; rated {model.rated_ah} Ah / {model.rated_wh} Wh.')
                    if state == 'FINISHED':
                        add('Totals frozen. Voltage/current above are separate live measurements.', alert)
                    elif bad:
                        add('Totals are last received; their current device values are unknown.', alert)
                    if s['reason'] != 'none':
                        add('End reason: ' + s['reason'], alert)
                    add()
                if view == 'summary':
                    add('FROZEN TEST SUMMARY  /  PageUp and PageDown scroll', accent)
                    for line in model.summary or ['No completed summary received yet.']:
                        add(line)
                else:
                    events_begin = len(content)
                    add('EVENTS  /  PageUp for history', accent)
                    # Tail by default, full history when scrolling. Raw view retains every line.
                    events = model.events if held_lines is not None else model.events[-max(3, height - len(content) - 8):]
                    for stamp, message in events:
                        add(stamp + '  ' + message)
            else:
                add('RAW SERIAL OUTPUT  /  received text; file logging preserves original bytes', accent)
                for line in model.raw + ([partial] if partial else []):
                    add(line)

            pane_height = height - 7
            if held_lines is not None:
                content = held_lines
            if view == 'raw' or held_lines is not None:
                scroll = min(scroll, max(0, len(content) - pane_height))
                end = max(pane_height, len(content) - scroll)
                visible = content[max(0, end - pane_height):end]
            else:
                visible = content[:pane_height]
            for row, (line, style) in enumerate(visible, 3):
                draw(row, line, style)
            draw(height - 4, '-' * (width - 1))
            draw(height - 3, 'F1 Dashboard | F2 Raw | F3 Summary | PgUp/PgDn scroll | F4 Live')
            draw(height - 2, 'Enter send | Up/Down history | Ctrl+U clear | Ctrl+C quit'
                 if held_lines is None else 'SCROLLBACK HELD - F4 returns to live view | Enter still sends commands',
                 colors.get('yellow', 0) if held_lines is not None else 0)
            available = width - 4
            start = max(0, cursor - available + 1)
            draw(height - 1, '> ' + command[start:start + available])
            try:
                screen.move(height - 1, 2 + cursor - start)
            except curses.error:
                pass
        screen.refresh()

        try:
            key = screen.get_wch()
        except curses.error:
            continue
        if key == '\x03':
            return
        if key in (curses.KEY_F1, curses.KEY_F2, curses.KEY_F3, curses.KEY_F4):
            if key != curses.KEY_F4:
                view = {curses.KEY_F1: 'dashboard', curses.KEY_F2: 'raw', curses.KEY_F3: 'summary'}[key]
            scroll = 0
            held_lines = None
            if view == 'dashboard' and not csv_wanted:
                csv_wanted = True
                model.csv_seen = False
                next_csv_request = now
        elif key == curses.KEY_RESIZE:
            held_lines = None
            scroll = 0
        elif key in (curses.KEY_PPAGE, curses.KEY_NPAGE):
            if held_lines is None:
                if view == 'dashboard':
                    content = content[:events_begin] if events_begin is not None else content[:]
                    add('EVENT HISTORY', accent)
                    for stamp, message in model.events:
                        add(stamp + '  ' + message)
                held_lines = content[:]
                # PageDown reveals clipped dashboard/summary fields from the top;
                # PageUp in raw/dashboard opens older event history from the tail.
                scroll = (0 if view == 'raw' or (view == 'dashboard' and key == curses.KEY_PPAGE)
                          else max(0, len(content) - (height - 7)))
            step = max(1, height - 9)
            scroll = max(0, min(max(0, len(held_lines) - (height - 7)),
                               scroll + (step if key == curses.KEY_PPAGE else -step)))
        elif key in ('\n', '\r', curses.KEY_ENTER):
            if not command.strip():
                continue
            if not connected:
                model.event('Cannot send: disconnected. No command sent.')
                continue
            if not send(command):
                continue
            if command.strip().lower() == 'human':
                csv_wanted = False
                view = 'raw'
                held_lines = None
            elif command.strip().lower() == 'csv':
                csv_wanted = True
                view = 'dashboard'
                held_lines = None
            if not history or history[-1] != command:
                history.append(command)
                history = history[-100:]
            history_index = len(history)
            command = draft = ''
            cursor = 0
        elif key == curses.KEY_UP and history_index > 0:
            if history_index == len(history):
                draft = command
            history_index -= 1
            command = history[history_index]
            cursor = len(command)
        elif key == curses.KEY_DOWN and history_index < len(history):
            history_index += 1
            command = history[history_index] if history_index < len(history) else draft
            cursor = len(command)
        elif key == curses.KEY_LEFT:
            cursor = max(0, cursor - 1)
        elif key == curses.KEY_RIGHT:
            cursor = min(len(command), cursor + 1)
        elif key in (curses.KEY_HOME, '\x01'):
            cursor = 0
        elif key in (curses.KEY_END, '\x05'):
            cursor = len(command)
        elif key in (curses.KEY_BACKSPACE, '\x7f', '\b') and cursor:
            command = command[:cursor - 1] + command[cursor:]
            cursor -= 1
        elif key == curses.KEY_DC:
            command = command[:cursor] + command[cursor + 1:]
        elif key == '\x15':
            command = ''
            cursor = 0
        elif isinstance(key, str) and ' ' <= key <= '~' and len(command) < 31:
            command = command[:cursor] + key + command[cursor:]
            cursor += 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('port', nargs='?', default='/dev/ttyACM1')
    parser.add_argument('--baud', type=int, default=115200)
    parser.add_argument('--log', help='Append all original received bytes to this file')
    args = parser.parse_args()
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.error('run this console in an interactive terminal')
    try:
        with ExitStack() as stack:
            log = stack.enter_context(open(args.log, 'ab')) if args.log else None
            port = stack.enter_context(serial.Serial(
                args.port, args.baud, timeout=0, write_timeout=1, exclusive=True))
            curses.wrapper(console, port, log)
    except KeyboardInterrupt:
        pass
    except (serial.SerialException, OSError, curses.error) as exc:
        print(f'Serial console: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
