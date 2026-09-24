# Copyright (c) 2026 Luke Repko
# SPDX-License-Identifier: GPL-3.0-or-later

"""Single serial owner. All methods are called under lock by API/worker."""
import codecs
import math
import threading
import time
from uuid import uuid4
import serial
from .protocol import Dashboard, FIELDS
from .storage import Store, stamp

class Recorder:
    def __init__(self, directory):
        self.lock=threading.RLock()
        self.store=Store(directory)
        self.model=Dashboard()
        self.port=None
        self.mode='disconnected'
        self.port_name=''
        self.baud=115200
        self.session=None
        self.raw_file=None
        self.decoder=codecs.getincrementaldecoder('utf-8')('replace')
        self.partial=''
        self.run=None
        self.device_key=None
        self.profile=None
        self.settings=None
        self.protocol=0
        self.pending=None
        self.csv_wanted=True
        self.next_csv=0
        self.next_settings=0
        self.summary_requested=False
        self.quit=threading.Event()
        self.demo_state='IDLE'
        self.demo_reason='none'
        self.demo_elapsed=0
        self.demo_number=0
        self.demo_ah=0.0
        self.demo_wh=0.0
        self.demo_last=0
        self.replay=None
        self.event('CellTrace ready. Connect between tests; opening USB may reset the device.')

    def event(self, text):
        self.store.event(self.run['id'] if self.run else None,text)

    def connect(self, port=None, demo=False, baud=115200):
        if self.mode!='disconnected': raise ValueError('Disconnect the current source first')
        self.model=Dashboard(); self.settings=None; self.protocol=0
        self.profile=None; self.pending=None; self.run=None; self.device_key=None
        self.decoder=codecs.getincrementaldecoder('utf-8')('replace'); self.partial=''
        self.csv_wanted=True; self.summary_requested=False
        self.session=str(uuid4()); self.baud=baud
        if not demo:
            self.port=serial.Serial(port,baud,timeout=0,write_timeout=1,exclusive=True)
        self.mode='demo' if demo else 'serial'
        self.port_name='SIMULATED • accelerated' if demo else port
        self.raw_file=open(self.store.directory/(self.session+'.log'),'ab',buffering=0)
        self.next_csv=time.monotonic()+2; self.next_settings=time.monotonic()+2
        self.event('Connected to '+self.port_name+'. No test started automatically.')
        if demo:
            self.demo_state='IDLE'; self.demo_reason='none'; self.demo_elapsed=0; self.demo_number=0; self.demo_ah=0; self.demo_wh=0
            self.feed(b'# CELLTRACE protocol 1\n# Settings: 20.000000,256.000000,10.000\n')

    def disconnect(self, why='Serial source disconnected; load is NOT switched off'):
        if self.run and self.run['status'] in ('ARMED','RUNNING'):
            self.run.update(status='INTERRUPTED', interruption=why,ended=stamp())
            self.store.save_run(self.run)
        self.event(why)
        if self.replay:
            self.replay.close();self.replay=None
        if self.port:
            self.port.close(); self.port=None
        if self.raw_file:
            self.raw_file.close(); self.raw_file=None
        self.mode='disconnected'; self.pending=None

    def send(self, command, automatic=False):
        if self.mode=='disconnected': raise ValueError('No serial connection')
        if self.mode=='replay': raise ValueError('Replay is read-only')
        if not command or len(command)>95 or any(ord(c)<32 or ord(c)>126 for c in command):
            raise ValueError('Command must be 1–95 printable ASCII characters')
        self.event(('Auto > ' if automatic else '> ')+command)
        if self.mode=='demo':
            self.demo_command(command)
        else:
            try:
                payload=(command+'\n').encode('ascii')
                if self.port.write(payload)!=len(payload): raise OSError('Incomplete serial write')
            except (OSError,serial.SerialException) as exc:
                self.disconnect('Send failed; delivery uncertain; command NOT retried: '+str(exc))
                raise ValueError('Delivery uncertain. Command was not retried.') from exc
        # A write is not an acknowledgement of acceptance by the firmware.

    def command(self, text):
        cmd=text.strip().lower()
        if self.pending and cmd=='stop': self.pending=None
        if self.pending: raise ValueError('Waiting for settings readback; commands are temporarily held')
        if cmd=='start':
            if not self.profile: raise ValueError('Select a profile and use Start test first')
            return self.start(self.profile)
        if cmd.startswith('configure'): raise ValueError('Use the profile settings workflow')
        if len(cmd)>31: raise ValueError('Console commands are limited to 31 characters')
        if cmd=='zero' and self.model.sample and self.model.sample['state'] in ('RUNNING','ARMED'):
            raise ValueError('Zero is unavailable during a test')
        self.send(cmd)
        if cmd in ('human','csv'):
            self.csv_wanted=cmd=='csv'
            if self.csv_wanted: self.model.csv_seen=False; self.next_csv=time.monotonic()+2

    def start(self, profile):
        if self.mode not in ('serial','demo'): raise ValueError('Connect a device or demo first')
        if self.pending: raise ValueError('A start request is already pending')
        if not self.model.sample or self.model.updated is None or time.monotonic()-self.model.updated>3:
            raise ValueError('Fresh device readings required')
        if self.model.sample['state'] in ('RUNNING','ARMED'): raise ValueError('A test is already active')
        if self.model.sample['valid']!='1': raise ValueError('Valid sensor readings required')
        if self.protocol<1: raise ValueError('CellTrace firmware required for configurable tests; legacy monitoring still works')
        self.profile=dict(profile)
        self.pending={'profile':dict(profile),'deadline':time.monotonic()+5}
        self.csv_wanted=True
        ah=profile.get('expected_ah') or 0; wh=profile.get('expected_wh') or 0
        self.send(f'configure {ah:.6f} {wh:.6f} {profile["endpoint_v"]:.3f}')

    def feed(self, data):
        if self.raw_file: self.raw_file.write(data)
        self.partial+=self.decoder.decode(data)
        while '\n' in self.partial:
            line,self.partial=self.partial.split('\n',1)
            self.accept(line.rstrip('\r'))
        if len(self.partial)>8192:
            self.event('Overlong line retained in raw log; parser skipped it'); self.partial=''

    def accept(self,line):
        now=time.monotonic()
        if line.startswith('# CELLTRACE protocol '):
            try:self.protocol=int(line.rsplit(' ',1)[1])
            except ValueError:pass
        if line.startswith('# Settings: '):
            try:
                ah,wh,endpoint=map(float,line.split(': ',1)[1].split(','))
                if not all(math.isfinite(x) for x in (ah,wh,endpoint)):raise ValueError()
                self.settings={'expected_ah':ah or None,'expected_wh':wh or None,'endpoint_v':endpoint}
                if self.pending:
                    p=self.pending['profile']
                    match=all(abs((p.get(k) or 0)-(self.settings.get(k) or 0))<0.0006 for k in self.settings)
                    self.pending=None
                    if match:
                        self.send('csv',True); self.send('start')
                    else:self.event('Settings readback mismatch; test NOT started')
            except ValueError:
                self.event('Invalid settings readback; test NOT started');self.pending=None
        if line.startswith('# RESET:'):
            if self.run and self.run['status'] in ('RUNNING','ARMED'):
                self.run.update(status='INTERRUPTED',interruption='Arduino reset; device totals lost',ended=stamp())
                self.store.save_run(self.run)
            self.event('Device reset; no automatic resume')
            self.run=None; self.device_key=None; self.pending=None; self.settings=None
            self.next_settings=now+2;self.next_csv=now+2
        before=self.model.updated
        previous=self.model.sample
        ev=len(self.model.events)
        summary_version=self.model.summary_version
        self.model.accept(line,now)
        for _,message in self.model.events[ev:]:
            self.event(message.replace('F3 to view all fields','open the test report').replace('F2 raw output','raw serial output'))
        # Raw bytes are durable on disk. Only a bounded window is kept for the live viewer.
        self.model.raw=self.model.raw[-1500:];self.model.events=self.model.events[-500:]
        s=self.model.sample
        if s and self.model.updated!=before:
            if previous and ((int(s['uptime_ms'])-int(previous['uptime_ms'])) % 2**32 > 2**31 or
                    (s['state'] in ('ARMED','RUNNING') and float(s['elapsed_s']) < float(previous['elapsed_s']) and s['test_id']==previous['test_id'])):
                if self.run and self.run['status'] in ('RUNNING','ARMED'):
                    self.run.update(status='INTERRUPTED',interruption='Device counters restarted; continuity unknown',ended=stamp())
                    self.store.save_run(self.run)
                self.event('Device counter restart detected; starting a separate recording segment')
                self.model.reset_count+=1;self.model.summary=[];self.model.collecting_summary=False;self.run=None;self.device_key=None;self.settings=None;self.pending=None
            key=(self.session,self.model.reset_count,s['test_id'])
            if s['state']!='IDLE' and key!=self.device_key:
                if self.run and self.run['status'] in ('RUNNING','ARMED'):
                    self.run.update(status='INTERRUPTED',interruption='New device test observed',ended=stamp());self.store.save_run(self.run)
                self.device_key=key; self.summary_requested=False
                p=self.profile or {'name':'Unassigned battery','battery_id':'','expected_ah':None,'expected_wh':None,'reference':[]}
                self.run={'id':str(uuid4()),'session':self.session,'profile':p,'started':stamp(),'ended':None,
                    'status':s['state'],'device_test_id':s['test_id'],'summary':self.model.summary.copy() if not self.model.collecting_summary else [], 'notes':'',
                    'source':self.mode,'settings':self.settings,'partial_capture':float(s['elapsed_s'])>1}
                self.event('Recording test '+self.run['id'])
            if self.run and key==self.device_key:
                # Frozen runs retain their last device result; post-test readings are separate.
                if self.run['status'] in ('ARMED','RUNNING') or not self.run.get('last'):
                    self.store.sample(self.run['id'],dict(s,received_at=stamp()))
                    self.run['last']=s.copy();self.run['status']=s['state']
                    if s['state']=='FINISHED':self.run['ended']=stamp()
                    self.store.save_run(self.run)
        if self.run and self.model.summary_version!=summary_version:
            self.run['summary']=self.model.summary.copy();self.store.save_run(self.run)

    def state(self):
        age=time.monotonic()-self.model.updated if self.model.updated is not None else None
        return {'mode':self.mode,'port':self.port_name,'baud':self.baud,'sample':self.model.sample,
            'age':age,'protocol':self.protocol,'settings':self.settings,'pending':bool(self.pending),
            'csv':self.csv_wanted,'current_run':self.run,'events':self.store.events(),
            'raw':self.model.raw[-300:],'profiles':self.store.profiles(),'runs':self.store.runs()}

    def tick(self):
        now=time.monotonic()
        if self.mode=='serial':
            try:
                data=self.port.read(min(self.port.in_waiting,8192))
                if data:self.feed(data)
            except (OSError,serial.SerialException) as exc:
                self.disconnect('USB failure: '+str(exc));return
        elif self.mode=='demo' and now-self.demo_last>=1:
            self.demo_last=now;self.demo_tick()
        elif self.mode=='replay' and now-self.demo_last>=0.1:
            self.demo_last=now
            data=self.replay.readline()
            if data:self.feed(data)
            else:
                self.replay.close();self.replay=None;self.disconnect('Replay ended; no hardware was accessed')
        if self.pending and now>self.pending['deadline']:
            self.pending=None;self.event('Settings readback timed out; test NOT started')
        if self.mode=='serial':
            if self.csv_wanted and not self.model.csv_seen and now>=self.next_csv:
                self.next_csv=now+3;self.send('csv',True)
            if self.protocol and self.settings is None and now>=self.next_settings:
                self.next_settings=now+3;self.send('settings',True)
            if self.model.sample and self.model.sample['state']=='FINISHED' and not self.model.summary and not self.summary_requested:
                self.summary_requested=True;self.send('summary',True)

    def worker(self):
        while not self.quit.wait(0.05):
            with self.lock:
                try:self.tick()
                except Exception as exc:
                    self.disconnect('Recorder error: '+str(exc))

    def start_replay(self,path):
        self.connect(demo=True)
        self.mode='replay';self.port_name='REPLAY • '+str(path)
        self.model=Dashboard();self.protocol=0;self.settings=None
        self.raw_file.close();self.raw_file=open(self.store.directory/(self.session+'.log'),'wb',buffering=0)
        self.replay=open(path,'rb');self.event('Read-only log replay started')

    def demo_command(self,cmd):
        if cmd.startswith('configure '):
            if self.demo_state in ('RUNNING','ARMED'):
                self.feed(b'# Configure refused: active test\n');return
            _,a,w,v=cmd.split()
            self.feed(f'# Settings: {a},{w},{v}\n'.encode())
        elif cmd=='settings':
            s=self.settings or {'expected_ah':20,'expected_wh':256,'endpoint_v':10}
            self.feed(f'# Settings: {s["expected_ah"] or 0},{s["expected_wh"] or 0},{s["endpoint_v"]}\n'.encode())
        elif cmd=='start':
            self.demo_number+=1;self.demo_state='RUNNING';self.demo_reason='none';self.demo_elapsed=0;self.demo_ah=0;self.demo_wh=0
            self.feed(b'# New simulated test started (60x time). No hardware connected.\n')
        elif cmd=='stop':
            if self.demo_state=='RUNNING':
                self.demo_state='FINISHED';self.demo_reason='manual_stop_partial';self.demo_tick();self.demo_summary(self.demo_reason)
        elif cmd=='summary':self.demo_summary(self.demo_reason if self.demo_state=='FINISHED' else 'none')
        elif cmd=='zero':self.feed(b'# Current offset A (SIMULATED): 0.000000\n')
        elif cmd=='csv':self.feed((','.join(FIELDS)+'\n').encode())
        elif cmd=='help':self.feed(b'# Commands: start stop status summary zero csv human help settings\n')
        elif cmd not in ('human','status'):self.feed(b'# Unknown command\n')

    def demo_tick(self):
        a=(self.settings or {}).get('expected_ah') or 20
        nominal=(self.profile or {}).get('nominal_v') or 12.8
        endpoint=(self.settings or {}).get('endpoint_v') or 10
        ratio=min(self.demo_ah/a,1)
        v=max(endpoint,nominal+0.5-0.55*ratio-2.75*ratio**12)
        current=v/2 if self.demo_state=='RUNNING' else 0
        if self.demo_state=='RUNNING':
            self.demo_elapsed+=60;self.demo_ah+=current/60;self.demo_wh+=v*current/60
        wh=(self.settings or {}).get('expected_wh')
        remain=f'{max(0,100*(1-self.demo_ah/a)):.2f}' if (self.settings or {}).get('expected_ah') else ''
        values=[self.demo_number,int(time.monotonic()*1000)%2**32,self.demo_state,1,f'{v:.4f}',f'{current:.4f}',f'{v*current:.4f}',
            f'{self.demo_elapsed:.3f}',f'{self.demo_ah:.6f}',f'{self.demo_ah*1000:.3f}',f'{self.demo_wh:.6f}',f'{self.demo_wh/1000:.8f}',remain,
            f'{self.demo_ah/a*100:.2f}' if remain else '',f'{self.demo_wh/wh*100:.2f}' if wh else '',
            self.demo_reason if self.demo_state=='FINISHED' else 'none']
        if self.csv_wanted:self.feed((','.join(map(str,values))+'\n').encode())
        else:self.feed(f'# Live: {v:.4f} V  {current:.4f} A  {v*current:.4f} W (SIMULATED)\n'.encode())
        if self.demo_state=='RUNNING' and (v<=endpoint or self.demo_ah>=a):
            self.demo_state='FINISHED';self.demo_reason='low_voltage_endpoint';self.demo_tick();self.demo_summary(self.demo_reason)

    def demo_summary(self,reason):
        if self.demo_state!='FINISHED':self.feed(b'# No finished test\n');return
        s=self.model.sample or {}
        fields={'Test number':self.demo_number,'End reason':reason,'Result':'SIMULATED DATA — no battery measurement',
            'Discharge elapsed':str(self.demo_elapsed)+' s','Discharged Ah':f'{self.demo_ah:.6f}',
            'Discharged mAh':f'{self.demo_ah*1000:.3f}','Delivered Wh':f'{self.demo_wh:.6f}',
            'Delivered kWh':f'{self.demo_wh/1000:.8f}','Average current A':self.demo_ah*3600/max(1,self.demo_elapsed),
            'Last valid voltage V':s.get('voltage_v',''), 'Valid integrated samples':'simulated'}
        lines=['# ===== CELLTRACE CAPACITY TEST SUMMARY =====']+[f'# {k}: {v}' for k,v in fields.items()]
        lines+=['# DISCONNECT LOAD. Software cannot switch off the resistor.','# Result is frozen in RAM until start/reset.']
        self.feed(('\n'.join(lines)+'\n').encode())
