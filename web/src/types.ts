// Copyright (c) 2026 Luke Repko
// SPDX-License-Identifier: GPL-3.0-or-later

export type Point = Record<string,string> & {seq:number};
export type Reference = {ah:number;voltage_v:number};
export type Profile = {id:string;name:string;battery_id:string;manufacturer:string;chemistry:string;cells:number|null;nominal_v:number|null;expected_ah:number|null;expected_wh:number|null;endpoint_v:number;notes:string;conditions:string;reference_label:string;reference_kind:string;reference:Reference[]};
export type Run = {id:string;profile:Profile;started:string;ended:string|null;status:string;summary:string[];last?:Point;notes:string;source:string;partial_capture:boolean;interruption?:string;settings?:Record<string,number>};
export type Event = {time:string;message:string};
export type State = {mode:string;port:string;baud:number;sample:Point|null;age:number|null;protocol:number;settings:Record<string,number>|null;pending:boolean;csv:boolean;current_run:Run|null;events:Event[];raw:string[];profiles:Profile[];runs:Run[]};
export const blankProfile:Profile={id:'',name:'',battery_id:'',manufacturer:'',chemistry:'',cells:null,nominal_v:null,expected_ah:null,expected_wh:null,endpoint_v:10,notes:'',conditions:'',reference_label:'',reference_kind:'user-defined model',reference:[]};
export const number=(s:Point|undefined|null,k:string):number|null=>s?.[k]!=='' && s?.[k]!=null && Number.isFinite(Number(s[k]))?Number(s[k]):null;
export function elapsed(value:number){const t=Math.max(0,Math.floor(value));return [Math.floor(t/3600),Math.floor(t/60)%60,t%60].map(v=>String(v).padStart(2,'0')).join(':');}
export function referenceAt(points:Reference[],ah:number):number|null{
 if(!points.length||ah<points[0].ah||ah>points[points.length-1].ah)return null;
 const i=points.findIndex(p=>p.ah>=ah);if(i===0)return points[0].voltage_v;
 const a=points[i-1],b=points[i];return a.voltage_v+(b.voltage_v-a.voltage_v)*(ah-a.ah)/(b.ah-a.ah);
}
export function parseReference(text:string):Reference[]{
 const lines=text.trim().split(/\r?\n/).filter(l=>l.trim());
 if(lines[0]?.toLowerCase().replaceAll(' ','')==='ah,voltage_v')lines.shift();
 const points=lines.map((line,i)=>{const cells=line.split(',').map(s=>s.trim());if(cells.length!==2||cells.some(s=>s===''))throw Error(`Reference row ${i+1}: expected ah,voltage_v`);const [ah,voltage_v]=cells.map(Number);if(!Number.isFinite(ah)||!Number.isFinite(voltage_v)||ah<0||voltage_v<0)throw Error(`Reference row ${i+1}: invalid values`);return{ah,voltage_v};});
 if(points.length && points.length<2)throw Error('A curve needs at least two points');
 if(points.some((p,i)=>i>0&&p.ah<=points[i-1].ah))throw Error('Reference Ah must be strictly increasing');return points;
}
export async function api(path:string,body?:unknown){const r=await fetch('/api'+path,body===undefined?undefined:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const data=await r.json();if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));return data;}

export function firmwareStartBlock(state:Pick<State,'mode'|'protocol'>|null):string|null {
 if(state?.mode==='serial'&&state.protocol<1)return 'Firmware update required: live readings are available, but starting a test needs CellTrace firmware. Disconnect the load, upload the CellTrace firmware, then reconnect.';
 if(state?.mode==='replay')return 'Log replay is read-only. Connect a device or the simulated source to start a new test.';
 return null;
}
