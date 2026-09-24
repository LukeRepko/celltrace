# Copyright (c) 2026 Luke Repko
# SPDX-License-Identifier: GPL-3.0-or-later

import argparse
import asyncio
from contextlib import asynccontextmanager
import csv
import io
from pathlib import Path
import threading
import webbrowser
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from serial.tools import list_ports
import uvicorn

from .models import Profile
from .protocol import FIELDS
from .service import Recorder

ROOT=Path(__file__).resolve().parents[2]

class Connection(BaseModel):
    port: str = ''
    demo: bool = False
    baud: int = Field(default=115200,ge=1200,le=2000000)
class Command(BaseModel):
    command: str
class Start(BaseModel):
    profile_id: str
class Notes(BaseModel):
    notes: str = Field(max_length=10000)


def create_app(recorder, demo=False, replay=None):
    @asynccontextmanager
    async def lifespan(app):
        with recorder.lock:
            if demo:recorder.connect(demo=True)
            if replay:recorder.start_replay(replay)
        thread=threading.Thread(target=recorder.worker,daemon=True);thread.start()
        yield
        recorder.quit.set();thread.join(timeout=2)
        with recorder.lock:
            recorder.disconnect('Recorder closed; hardware load is NOT switched off')
            recorder.store.close()

    app=FastAPI(title='CellTrace',lifespan=lifespan)

    def allowed(origin,host):
        if not origin:return True
        parsed=urlparse(origin)
        return parsed.scheme=='http' and parsed.netloc==host

    @app.middleware('http')
    async def local_only(request:Request,call_next):
        if request.url.hostname not in ('127.0.0.1','localhost','testserver'):
            return JSONResponse({'detail':'Local access only'},status_code=403)
        if request.method not in ('GET','HEAD','OPTIONS') and not allowed(request.headers.get('origin'),request.headers.get('host')):
            return JSONResponse({'detail':'Cross-origin commands are disabled'},status_code=403)
        return await call_next(request)

    @app.exception_handler(ValueError)
    async def invalid(request,exc):return JSONResponse({'detail':str(exc)},status_code=409)

    @app.exception_handler(OSError)
    async def transport_error(request,exc):return JSONResponse({'detail':str(exc)},status_code=409)

    @app.get('/api/state')
    def state():
        with recorder.lock:return recorder.state()

    @app.get('/api/ports')
    def ports():return [{'device':p.device,'description':p.description} for p in list_ports.comports()]

    @app.post('/api/connect')
    def connect(value:Connection):
        with recorder.lock:
            if not value.demo and not value.port:raise ValueError('Select a serial port')
            recorder.connect(value.port,value.demo,value.baud)
            return {'message':'Connected; no test started'}

    @app.post('/api/disconnect')
    def disconnect():
        with recorder.lock:
            recorder.disconnect();return {'message':'Disconnected; load is NOT switched off'}

    @app.post('/api/command')
    def command(value:Command):
        with recorder.lock:
            recorder.command(value.command);return {'message':'Command sent; watch the device response'}

    @app.post('/api/start')
    def start(value:Start):
        with recorder.lock:
            p=next((p for p in recorder.store.profiles() if p['id']==value.profile_id),None)
            if not p:raise ValueError('Select a saved battery profile')
            recorder.start(p);return {'message':'Settings sent; waiting for readback before starting'}

    @app.post('/api/profiles')
    def profile(value:Profile):
        with recorder.lock:return recorder.store.save_profile(value.model_dump())

    def get_run(key):
        r=recorder.store.get_run(key)
        if not r:raise HTTPException(404,'Test not found')
        return r

    @app.get('/api/runs/{key}')
    def run(key:str):
        with recorder.lock:return {'run':get_run(key),'points':recorder.store.samples(key),'events':recorder.store.events(key,100000)}

    @app.post('/api/runs/{key}/notes')
    def notes(key:str,value:Notes):
        with recorder.lock:
            r=get_run(key);r['notes']=value.notes;recorder.store.save_run(r)
            if recorder.run and recorder.run['id']==key:recorder.run=r
            return r

    @app.get('/api/runs/{key}/samples.csv')
    def export(key:str):
        with recorder.lock:
            get_run(key);rows=recorder.store.samples(key)
        buf=io.StringIO();writer=csv.DictWriter(buf,fieldnames=FIELDS+['received_at'],extrasaction='ignore')
        writer.writeheader();writer.writerows(rows)
        return Response(buf.getvalue(),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="celltrace-{key}.csv"'})

    @app.get('/api/runs/{key}/serial.log')
    def raw(key:str):
        with recorder.lock:r=get_run(key)
        path=recorder.store.directory/(r['session']+'.log')
        if not path.exists():raise HTTPException(404,'Raw log unavailable')
        return FileResponse(path,media_type='application/octet-stream',filename='celltrace-session-'+r['session']+'.log')

    @app.websocket('/ws')
    async def ws(socket:WebSocket):
        if not allowed(socket.headers.get('origin'),socket.headers.get('host')):
            await socket.close(code=1008);return
        await socket.accept();last_run=None;cursor=0
        try:
            while True:
                with recorder.lock:
                    state=recorder.state();run=state['current_run'];key=run['id'] if run else None
                    if key!=last_run:cursor=0;last_run=key
                    points=recorder.store.samples(key,cursor) if key else []
                    if points:cursor=points[-1]['seq']
                await socket.send_json({'state':state,'points':points})
                await asyncio.sleep(0.5)
        except (WebSocketDisconnect,RuntimeError,OSError):pass

    dist=ROOT/'web/dist'
    if dist.exists():app.mount('/',StaticFiles(directory=dist,html=True),name='dashboard')
    else:
        @app.get('/')
        def missing():return JSONResponse({'detail':'Build web/ with npm run build first'},status_code=503)
    return app


def main():
    parser=argparse.ArgumentParser(description='CellTrace local battery workbench')
    parser.add_argument('--open',action='store_true',help='Open the local dashboard in your browser')
    parser.add_argument('--data',default=str(ROOT/'.celltrace'))
    parser.add_argument('--port',type=int,default=8765,help='Local HTTP port (not serial port)')
    group=parser.add_mutually_exclusive_group();group.add_argument('--demo',action='store_true');group.add_argument('--replay',type=Path)
    args=parser.parse_args();recorder=Recorder(args.data)
    print(f'CellTrace: http://127.0.0.1:{args.port} — serial remains closed until you connect')
    if args.open:
        threading.Timer(1.0,lambda:webbrowser.open(f'http://127.0.0.1:{args.port}')).start()
    uvicorn.run(create_app(recorder,args.demo,args.replay),host='127.0.0.1',port=args.port,workers=1)

if __name__=='__main__':main()
