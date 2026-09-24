# Copyright (c) 2026 Luke Repko
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import fcntl
import sqlite3
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone


def stamp():
    return datetime.now(timezone.utc).isoformat()

class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.owner_lock = open(self.directory/'recorder.lock', 'a')
        try:
            fcntl.flock(self.owner_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.owner_lock.close()
            raise ValueError('Another CellTrace recorder owns this data directory; open its dashboard instead')
        self.db = sqlite3.connect(self.directory/'celltrace.sqlite3', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS profiles(id TEXT PRIMARY KEY, document TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, document TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS samples(id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, document TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS samples_run ON samples(run_id,id);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, run_id TEXT, document TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id,id);
        PRAGMA optimize;
        ''')
        # A service restart never silently resumes a previously active run.
        for run in self.runs():
            if run['status'] in ('ARMED', 'RUNNING'):
                run.update(status='INTERRUPTED', interruption='Recorder stopped; continuity unknown', ended=stamp())
                self.save_run(run)

    def close(self):
        self.db.close()
        self.owner_lock.close()

    def profiles(self):
        return [json.loads(r[0]) for r in self.db.execute('SELECT document FROM profiles ORDER BY rowid')]

    def save_profile(self, value):
        value = dict(value)
        value['id'] = value.get('id') or str(uuid4())
        self.db.execute('INSERT OR REPLACE INTO profiles VALUES (?,?)', (value['id'], json.dumps(value)))
        self.db.commit()
        return value

    def save_run(self, value):
        self.db.execute('INSERT OR REPLACE INTO runs VALUES (?,?)', (value['id'], json.dumps(value)))
        self.db.commit()

    def get_run(self, key):
        r=self.db.execute('SELECT document FROM runs WHERE id=?', (key,)).fetchone()
        return json.loads(r[0]) if r else None

    def runs(self):
        return [json.loads(r[0]) for r in self.db.execute('SELECT document FROM runs ORDER BY rowid DESC')]

    def sample(self, run_id, value):
        cursor=self.db.execute('INSERT INTO samples(run_id,document) VALUES (?,?)', (run_id,json.dumps(value)))
        self.db.commit()
        return cursor.lastrowid

    def samples(self, run_id, after=0):
        return [dict(json.loads(r['document']), seq=r['id']) for r in self.db.execute(
            'SELECT id,document FROM samples WHERE run_id=? AND id>? ORDER BY id', (run_id,after))]

    def event(self, run_id, text):
        value={'time':stamp(), 'message':text}
        self.db.execute('INSERT INTO events(run_id,document) VALUES (?,?)', (run_id,json.dumps(value)))
        self.db.commit()

    def events(self, run_id=None, limit=300):
        sql='SELECT document FROM events'+(' WHERE run_id=?' if run_id else '')+' ORDER BY id DESC LIMIT ?'
        args=(run_id,limit) if run_id else (limit,)
        return list(reversed([json.loads(r[0]) for r in self.db.execute(sql,args)]))
