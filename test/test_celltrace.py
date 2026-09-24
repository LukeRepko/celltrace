# Copyright (c) 2026 Luke Repko
# SPDX-License-Identifier: GPL-3.0-or-later

"""Recording, replay, profiles and API invariants; no physical serial access."""
import io
import os
import pty
import tempfile
import time
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from celltrace.service import Recorder
from celltrace.models import Profile
from celltrace.server import create_app
from celltrace.protocol import Dashboard
from celltrace.storage import Store


def row(n=1,state='RUNNING',valid='1',elapsed='1.000',uptime=1000):
    return f'{n},{uptime},{state},{valid},'+('13.3487,6.4200,85.6987,' if valid=='1' else ',,,')+f'{elapsed},0.001783,1.783,0.023805,0.00002381,99.99,0.01,0.01,none\n'

class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.r=Recorder(self.temp.name)
        self.r.connect(demo=True)
    def tearDown(self):
        self.r.disconnect();self.r.store.close();self.temp.cleanup()
    def profile(self,**kw):
        return Profile(name='Pack A',expected_ah=20,expected_wh=256,endpoint_v=10,**kw).model_dump()
    def test_configure_readback_before_start(self):
        self.r.demo_tick();self.r.start(self.profile())
        self.assertEqual(self.r.demo_state,'RUNNING');self.assertIsNone(self.r.pending)
        self.r.demo_tick();self.assertEqual(self.r.run['profile']['name'],'Pack A')
        with self.assertRaises(ValueError):self.r.start(self.profile())
    def test_missing_settings_ack_never_starts(self):
        self.r.demo_tick()
        with patch.object(self.r,'send') as send:
            self.r.start(self.profile());self.r.pending['deadline']=0;self.r.tick()
            self.assertEqual(send.call_count,1);self.assertTrue(send.call_args.args[0].startswith('configure'))
    def test_settings_mismatch_never_starts(self):
        self.r.demo_tick()
        with patch.object(self.r,'send') as send:
            self.r.start(self.profile());self.r.accept('# Settings: 10,128,9')
            self.assertIsNone(self.r.pending);self.assertEqual(send.call_count,1)
    def test_legacy_can_monitor_but_cannot_configure(self):
        self.r.protocol=0;self.r.feed(row(state='IDLE').encode())
        with self.assertRaisesRegex(ValueError,'firmware'):self.r.start(self.profile())
        self.r.feed(row().encode());self.assertIsNotNone(self.r.run)
    def test_raw_bytes_split_utf8_and_invalid_readings_preserved(self):
        raw=b'# raw \x1b[2J \xff\r\n'+row().encode()+row(valid='0',uptime=2000,elapsed='2.000').encode()
        self.r.feed(raw[:15]);self.r.feed(raw[15:])
        log=Path(self.temp.name,self.r.session+'.log').read_bytes();self.assertTrue(log.endswith(raw))
        points=self.r.store.samples(self.r.run['id']);self.assertEqual(points[0]['voltage_v'],'13.3487')
        self.assertEqual(points[-1]['voltage_v'],'')
    def test_reset_creates_distinct_test_identity(self):
        self.r.feed(row().encode());first=self.r.run['id']
        self.r.feed(b'# RESET: device restarted\n');self.r.feed(row().encode())
        self.assertNotEqual(first,self.r.run['id']);self.assertEqual(self.r.store.get_run(first)['status'],'INTERRUPTED')
    def test_frozen_totals_and_summary_survive_new_live_readings(self):
        self.r.feed(row().encode());self.r.feed(row(state='FINISHED',elapsed='2.000').encode())
        self.r.feed(b'# ===== CELLTRACE CAPACITY TEST SUMMARY =====\n# Delivered Wh: 0.023805\n# Result is frozen in RAM until start/reset.\n')
        runid=self.r.run['id'];count=len(self.r.store.samples(runid))
        self.r.feed(row(state='FINISHED',elapsed='99.000',uptime=3000).encode())
        self.assertEqual(self.r.run['last']['elapsed_s'],'2.000');self.assertEqual(len(self.r.store.samples(runid)),count)
        self.assertIn('Delivered Wh: 0.023805',self.r.run['summary'])
    def test_profile_edit_cannot_rewrite_snapshot(self):
        p=self.r.store.save_profile(self.profile());self.r.profile=p;self.r.feed(row().encode())
        self.r.store.save_profile(dict(p,name='Changed',expected_ah=99))
        self.assertEqual(self.r.store.get_run(self.r.run['id'])['profile']['expected_ah'],20)
    def test_restart_marks_run_interrupted(self):
        self.r.feed(row().encode());key=self.r.run['id'];self.r.store.close();self.r.store=Store(self.temp.name)
        self.assertEqual(self.r.store.get_run(key)['status'],'INTERRUPTED')
    def test_serial_failure_no_automatic_reconnect(self):
        self.r.feed(row().encode());self.r.mode='serial';self.r.port=Mock();self.r.port.in_waiting=1;self.r.port.read.side_effect=OSError('USB unplugged')
        self.r.tick();self.assertEqual(self.r.mode,'disconnected');self.assertEqual(self.r.run['status'],'INTERRUPTED')
    def test_startup_noise_does_not_disconnect_or_replace_readings(self):
        self.r.feed(row().encode())
        previous=self.r.model.sample.copy()
        noise=b'\x01garbage\rbroken,line\xff\n'
        self.r.feed(noise)
        self.assertEqual(self.r.mode,'demo')
        self.assertEqual(self.r.model.sample,previous)
        self.assertTrue(Path(self.temp.name,self.r.session+'.log').read_bytes().endswith(noise))
        self.r.feed(row(elapsed='2.000',uptime=2000).encode())
        self.assertEqual(self.r.model.sample['elapsed_s'],'2.000')

    def test_unknown_ratings_parse_as_blanks(self):
        p=Dashboard();p.accept('1,1000,RUNNING,1,12,6,72,1,0.1,100,1.2,0.0012,,,,none',1)
        self.assertIsNotNone(p.sample);self.assertEqual(p.sample['rated_ah_pct'],'')
    def test_profile_reference_validation(self):
        with self.assertRaises(ValueError):Profile(name='Bad',endpoint_v=10,reference=[{'ah':1,'voltage_v':13},{'ah':0,'voltage_v':12}])
        with self.assertRaises(ValueError):Profile(name='Bad',endpoint_v=16)
    def test_second_recorder_cannot_rewrite_active_session(self):
        self.r.feed(row().encode())
        with self.assertRaisesRegex(ValueError,'Another CellTrace'):Store(self.temp.name)
        self.assertEqual(self.r.run['status'],'RUNNING')
    def test_counter_restart_without_banner_splits_runs(self):
        self.r.feed(row(uptime=5000,elapsed='5.000').encode());key=self.r.run['id']
        self.r.feed(row(uptime=1000,elapsed='1.000').encode())
        self.assertNotEqual(key,self.r.run['id']);self.assertEqual(self.r.store.get_run(key)['status'],'INTERRUPTED')
    def test_uptime_rollover_does_not_split_run(self):
        self.r.feed(row(uptime=2**32-100,elapsed='5.000').encode());key=self.r.run['id']
        self.r.feed(row(uptime=900,elapsed='6.000').encode());self.assertEqual(key,self.r.run['id'])
    def test_real_serial_transport_using_pseudo_terminal(self):
        self.r.disconnect()
        master,slave=pty.openpty()
        os.set_blocking(master,False)
        try:
            self.r.connect(os.ttyname(slave))
            os.write(master,b'# CELLTRACE protocol 1\n# Settings: 20,256,10\n'+row(state='IDLE').encode())
            for _ in range(50):
                self.r.tick()
                if self.r.model.sample:break
                time.sleep(0.01)
            self.assertIsNotNone(self.r.model.sample)
            self.r.start(self.profile())
            sent=os.read(master,1024)
            self.assertIn(b'configure 20.000000 256.000000 10.000',sent)
            self.assertNotIn(b'start',sent)
            os.write(master,b'# Settings: 20.000000,256.000000,10.000\n')
            for _ in range(50):
                self.r.tick()
                if self.r.pending is None:break
                time.sleep(0.01)
            sent=os.read(master,1024)
            self.assertIn(b'csv\nstart\n',sent)
        finally:
            self.r.disconnect();os.close(master);os.close(slave)
    def test_replay_is_read_only_and_keeps_original_bytes(self):
        self.r.disconnect()
        original=b'# imported session\r\n'+row().encode()
        source=Path(self.temp.name,'source.log');source.write_bytes(original)
        self.r.start_replay(source)
        with self.assertRaisesRegex(ValueError,'read-only'):self.r.command('stop')
        session=self.r.session
        for _ in range(5):
            self.r.demo_last=0;self.r.tick()
        self.assertEqual(self.r.mode,'disconnected')
        self.assertEqual(Path(self.temp.name,session+'.log').read_bytes(),original)
        self.assertEqual(self.r.run['status'],'INTERRUPTED')
    def test_invalid_port_returns_useful_error(self):
        self.r.disconnect();client=TestClient(create_app(self.r))
        reply=client.post('/api/connect',json={'port':'/nonexistent/celltrace-test-port'})
        self.assertEqual(reply.status_code,409);self.assertIn('detail',reply.json())
        self.assertEqual(self.r.mode,'disconnected')
    def test_api_exports_and_origin_check(self):
        self.r.feed(row().encode());key=self.r.run['id'];client=TestClient(create_app(self.r))
        self.assertEqual(client.post('/api/command',json={'command':'stop'},headers={'origin':'https://evil.example'}).status_code,403)
        text=client.get(f'/api/runs/{key}/samples.csv').text;self.assertIn('13.3487',text)
        self.assertEqual(client.get(f'/api/runs/{key}/serial.log').status_code,200)
        self.assertEqual(client.get('/api/runs/not-a-run').status_code,404)
        saved=client.post('/api/profiles',json=self.profile());self.assertEqual(saved.status_code,200)
    def test_browser_snapshot_does_not_open_or_reset_serial(self):
        self.r.feed(row().encode());client=TestClient(create_app(self.r));key=self.r.run['id']
        with patch('serial.Serial') as port:
            for _ in range(3):self.assertEqual(client.get('/api/state').json()['current_run']['id'],key)
            port.assert_not_called()

if __name__=='__main__':unittest.main()
