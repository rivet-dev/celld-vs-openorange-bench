#!/usr/bin/env python3
"""Wake a retired smoke-test agent, read its SQLite rows, then retire it again."""
import argparse,gzip,json,shlex
from common import ROOT,inventory,ssh
p=argparse.ArgumentParser();p.add_argument('run');p.add_argument('platform',choices=['rivet','celld']);a=p.parse_args()
base=ROOT/'.local/smoke'/a.run/a.platform
records=[json.loads(line) for path in base.glob('shard-*-events.ndjson*') for line in (gzip.open(path,'rt') if path.suffix=='.gz' else path.open())]
actor=next(e['actor'] for e in records if e['type']=='retired')
host=inventory()['loadgens'][0];node=inventory()['nodes'][0]
program=r'''import WebSocket from 'ws';
const config=JSON.parse(process.env.PROBE_CONFIG);
const u=new URL(config.endpoint);u.protocol='ws:';
if(config.platform==='rivet'){
 u.pathname='/gateway/loadActor/websocket';
 u.search=new URLSearchParams({'rvt-namespace':'default','rvt-method':'getOrCreate','rvt-pool':'default','rvt-key':config.actor,'rvt-token':process.env.RIVET_TOKEN});
}else u.pathname='/'+encodeURIComponent(config.actor);
const ws=new WebSocket(u);let inspected=false;
const timer=setTimeout(()=>{console.error('Probe timed out');process.exit(1)},30000);
ws.on('error',()=>{console.error('Probe socket error');process.exit(1)});
ws.on('message',raw=>{
 const m=JSON.parse(raw);
 if(m.type==='connected')ws.send(JSON.stringify({type:'inspect'}));
 if(m.type==='inspection'){
  const rows=Array.isArray(m.rows)?m.rows:m.rows.rows;
  if(!rows?.length||Number(rows[0].rows)<=0)throw Error('Retired SQLite data missing');
  inspected=true;console.log(JSON.stringify({platform:config.platform,actor:config.actor,retained_rows:Number(rows[0].rows),last_seq:Number(rows[0].last_seq)}));
  ws.send(JSON.stringify({type:'retire'}));
 }
});
ws.on('close',()=>{clearTimeout(timer);process.exit(inspected?0:1)});
'''
config={'platform':a.platform,'actor':actor,'endpoint':f"http://{node['private_ip']}:{6420 if a.platform=='rivet' else 8080}"}
command='cd /opt/rivet-celld-bench; set -a; . ./driver.env; set +a; PROBE_CONFIG='+shlex.quote(json.dumps(config))+' node --input-type=module -'
result=ssh(host,command,input=program,timeout=45)
proof=json.loads(result.stdout.strip());(base/'retained-data-proof.json').write_text(json.dumps(proof,indent=2)+'\n')
print(json.dumps(proof))
