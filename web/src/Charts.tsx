// Copyright (c) 2026 Luke Repko
// SPDX-License-Identifier: GPL-3.0-or-later

import {useEffect,useRef,useState} from 'react';
import * as echarts from 'echarts';
import type {Point,Profile,Run} from './types';
import {number,referenceAt} from './types';
import {Download,Expand,Radio} from 'lucide-react';

type Props={points:Point[];profile?:Profile;baseline?:{run:Run;points:Point[]};};
const colors=['#b7ed82','#73c8ef','#dba2f7','#f5bc70'];
export function Charts({points,profile,baseline}:Props){
 const [kind,setKind]=useState('voltage');const [windowSize,setWindowSize]=useState(0);const [following,setFollowing]=useState(true);
 const el=useRef<HTMLDivElement>(null);const chart=useRef<echarts.ECharts|null>(null);const followingRef=useRef(true);const zoom=useRef<any>(null);const legend=useRef<Record<string,boolean>>({});
 useEffect(()=>{const c=echarts.init(el.current!);chart.current=c;c.group='celltrace';const obs=new ResizeObserver(()=>c.resize());obs.observe(el.current!);c.on('legendselectchanged',(e:any)=>{legend.current=e.selected;});c.on('datazoom',()=>{followingRef.current=false;setFollowing(false);zoom.current=(c.getOption().dataZoom as any[])?.[0];});return()=>{obs.disconnect();c.dispose();};},[]);
 useEffect(()=>{
  const c=chart.current;if(!c)return;
  const capacity=kind==='reference';const isLoad=kind==='load';const isTotals=kind==='totals';
  const xmax=points.length?Number(points[points.length-1].elapsed_s):0;
  const series:any[]=[];
  function line(name:string,field:string,color:string,axis=0,source=points,dashed=false){
   const data:(number|null)[][]=[];let previous:Point|undefined;
   for(const p of source){
    const x=Number(p[capacity?'ah':'elapsed_s']);const val=number(p,field);
    const valid=!['voltage_v','current_a','power_w'].includes(field)||p.valid==='1';
    if(previous&&Date.parse(p.received_at)-Date.parse(previous.received_at)>3500)data.push([x,null]);
    data.push([x,valid?val:null]);previous=p;
   }
   series.push({name,type:'line',data,yAxisIndex:axis,showSymbol:source.length===1,connectNulls:false,smooth:false,lineStyle:{width:2,color,type:dashed?'dashed':'solid'},itemStyle:{color},emphasis:{focus:'series'},sampling:'minmax'});
  }
  if(isLoad){line('Current · A','current_a',colors[1]);line('Power · W','power_w',colors[3],1);}
  else if(isTotals){line('Capacity · Ah','ah',colors[0]);line('Energy · Wh','wh',colors[2],1);}
  else line('Actual · V','voltage_v',colors[0]);
  if(baseline){
   if(isLoad){line('Baseline current · A','current_a','#56849b',0,baseline.points,true);line('Baseline power · W','power_w','#907850',1,baseline.points,true);}
   else if(isTotals){line('Baseline capacity · Ah','ah','#6a9450',0,baseline.points,true);line('Baseline energy · Wh','wh','#9372a6',1,baseline.points,true);}
   else line('Baseline · V','voltage_v',colors[2],0,baseline.points,true);
  }
  if(capacity&&profile?.reference?.length)series.push({name:'Reference · V',type:'line',showSymbol:false,data:profile.reference.map(p=>[p.ah,p.voltage_v]),lineStyle:{color:colors[1],type:'dashed',width:2},itemStyle:{color:colors[1]},smooth:false});
  const markers:any[]=[];
  if(capacity&&profile?.expected_ah)markers.push({xAxis:profile.expected_ah,name:`Expected ${profile.expected_ah} Ah`});
  if(isTotals&&profile?.expected_ah)markers.push({yAxis:profile.expected_ah,name:`Expected ${profile.expected_ah} Ah`});
  if(!isTotals&&!isLoad&&profile?.endpoint_v)markers.push({yAxis:profile.endpoint_v,name:`Endpoint ${profile.endpoint_v} V`});
  if(series[0])series[0].markLine={silent:true,symbol:'none',label:{formatter:'{b}',color:'#a7afb0',fontSize:10,position:'insideEndTop'},lineStyle:{color:'#566162',type:'dashed'},data:markers};
  if(isTotals&&profile?.expected_wh)series[1].markLine={symbol:'none',data:[{yAxis:profile.expected_wh,name:`Expected ${profile.expected_wh} Wh`}],label:{formatter:'{b}',color:colors[2]},lineStyle:{color:colors[2],type:'dashed'}};
  const axis=(name:string,index=0)=>({type:'value',name,nameTextStyle:{color:colors[index],padding:[0,0,8,0]},scale:!isTotals,axisLabel:{color:'#8e9c9c',fontFamily:'monospace'},splitLine:{show:index===0,lineStyle:{color:'#263033'}},nameLocation:'end'});
  const dataZoom=followingRef.current?{start:0,end:100,...(!capacity&&windowSize?{startValue:Math.max(0,xmax-windowSize),endValue:xmax||1}:{})}:(zoom.current||{start:0,end:100});
  c.setOption({animation:false,color:colors,backgroundColor:'transparent',grid:{left:58,right:(isLoad||isTotals)?65:35,top:55,bottom:90},legend:{selected:legend.current,top:0,textStyle:{color:'#aab7b7'},icon:'roundRect'},
   tooltip:{trigger:'axis',formatter:capacity?(items:any[])=>{
    const actual=items.find(p=>p.seriesName==='Actual · V');
    if(!actual)return items.map(p=>`${p.seriesName}: ${p.value[1]??'No reading'} V`).join('<br/>');
    const [ah,voltage]=actual.value;const expected=profile?.reference?referenceAt(profile.reference,ah):null;
    return `Discharged: ${ah} Ah<br/>Actual: ${voltage??'No reading'} V`+(expected===null?'':`<br/>Reference: ${expected.toFixed(4)} V`+(voltage===null?'':`<br/>Difference: ${(voltage-expected).toFixed(4)} V`));
   }:undefined,backgroundColor:'#1a2326',borderColor:'#425053',textStyle:{color:'#eef4f4'},axisPointer:{type:'cross'},valueFormatter:(v:any)=>v==null?'No reading':String(v)},
   xAxis:{type:'value',name:capacity?'Discharged capacity · Ah':'Elapsed · min',nameLocation:'middle',nameGap:30,axisLabel:{color:'#8e9c9c',formatter:(v:number)=>capacity?String(v):String(Number((v/60).toFixed(1)))},splitLine:{show:false}},
   yAxis:isLoad?[axis('A',1),axis('W',3)]:isTotals?[axis('Ah'),axis('Wh',2)]:[axis('V')],
   dataZoom:[{type:'slider',bottom:16,height:18,borderColor:'#263033',fillerColor:'#b7ed8215',textStyle:{color:'#8e9c9c'},...dataZoom},{type:'inside',...dataZoom}],series}, {notMerge:true});
 },[points,profile,baseline,kind,windowSize]);
 const last=points[points.length-1];const ref=profile?.reference&&last?referenceAt(profile.reference,Number(last.ah)):null;
 return <section className="panel charts"><div className="panel-head"><div><span className="eyebrow">DISCHARGE ANALYSIS</span><h2>{kind==='reference'?'Expected meets measured':'Every reading tells a story'}</h2></div><div className="chart-actions"><button className="icon" title="Export chart PNG" onClick={()=>{const a=document.createElement('a');a.href=chart.current!.getDataURL({type:'png',pixelRatio:2,backgroundColor:'#141b1e'});a.download='celltrace-chart.png';a.click();}}><Download size={17}/></button><button className="icon" title="Reset chart zoom" onClick={()=>{followingRef.current=true;setFollowing(true);setWindowSize(0);chart.current?.dispatchAction({type:'dataZoom',start:0,end:100});followingRef.current=true;setFollowing(true);}}><Expand size={17}/></button></div></div>
 <div className="chart-toolbar"><div className="segments">{[['voltage','Voltage'],['load','Current / power'],['totals','Capacity / energy'],['reference','Expected vs actual']].map(([key,label])=><button className={kind===key?'selected':''} key={key} onClick={()=>{zoom.current=null;followingRef.current=true;setFollowing(true);setKind(key);}}>{label}</button>)}</div><select aria-label="Chart time range" value={windowSize} onChange={e=>{followingRef.current=true;setFollowing(true);setWindowSize(Number(e.target.value));}}><option value={0}>Whole test</option><option value={300}>Last 5 minutes</option><option value={1800}>Last 30 minutes</option></select></div>
 <div ref={el} className="chart-canvas" role="img" aria-label={`${kind} discharge chart; exact readings available in CSV export`}/>
 {!points.length&&<p className="empty-chart">The trace begins when a test starts. Explore safely with the simulated source.</p>}
 <div className="chart-foot"><span>{kind==='reference'?(profile?.reference?.length?`${profile.reference_kind} · ${profile.reference_label||'Unnamed reference'}`:'Capacity target only · add reference points to compare voltage curves'):'Original serial samples · gaps stay visible · device-integrated totals'}</span><button onClick={()=>{followingRef.current=true;setFollowing(true);setWindowSize(windowSize===0?300:0);}}><Radio size={12}/>{following?'Following live':'Resume live'}</button></div>
 {kind==='reference'&&ref!==null&&last?.valid==='1'&&<p className="chart-note">At {last.ah} Ah: reference {ref.toFixed(4)} V · actual {last.voltage_v} V · difference {(Number(last.voltage_v)-ref).toFixed(4)} V. Reference is interpolated only within its supplied range.</p>}
 {baseline&&<p className="chart-note">Baseline: {baseline.run.profile.name} · {baseline.run.profile.conditions||'Conditions unspecified'} · endpoint {baseline.run.profile.endpoint_v??'unknown'} V. Compare load and conditions before interpreting differences.</p>}
 </section>;
}
