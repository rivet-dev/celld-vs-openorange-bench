#!/usr/bin/env python3
"""Export Azure's per-minute Blob request counters for an existing measured run."""
import argparse,datetime,json,subprocess,time
from common import ROOT,LOCAL
from measurements import completed_levels

def iso(ms): return datetime.datetime.fromtimestamp(ms/1000,datetime.timezone.utc).isoformat()

def query(resource,metric,start,end,aggregation='Total',by_api=False):
    args=['az','monitor','metrics','list','--resource',resource,'--metric',metric,'--interval','PT1M','--aggregation',aggregation,'--start-time',iso(start),'--end-time',iso(end),'-o','json']
    if by_api: args += ['--filter',"ApiName eq '*'",'--top','100']
    return json.loads(subprocess.check_output(args,text=True))

def sanitize(raw):
    # Preserve exact timestamps, API names and values, without subscription/account identifiers.
    return {'timespan':raw.get('timespan'),'interval':raw.get('interval'),'metrics':[{'name':m['name']['value'],'unit':m['unit'],'timeseries':m['timeseries']} for m in raw['value']]}

def main():
    p=argparse.ArgumentParser();p.add_argument('run');p.add_argument('--platform',choices=['rivet','celld','both'],default='both');p.add_argument('--wait',action='store_true',help='Allow three minutes after the last hold for Azure Monitor delivery');a=p.parse_args()
    storage=json.loads((LOCAL/'storage.json').read_text())
    for platform in (['rivet','celld'] if a.platform=='both' else [a.platform]):
        dest=ROOT/'results'/a.run/platform; meta=json.loads((dest/'run.json').read_text())
        completed_levels(meta)
        if a.wait:time.sleep(max(0,meta['stages'][-1]['end_ms']/1000+180-time.time()))
        # Status polls have their own timestamps; report write totals come from
        # per-write events inside the exact UTC hold window.
        for stage in meta['stages']:
            if 'acknowledged_writes' in stage:
                stage['counter_delta_writes']=stage.pop('acknowledged_writes')
        (dest/'run.json').write_text(json.dumps(meta,indent=2)+'\n')
        start=meta['started_ms']//60000*60000;end=((meta['finished_ms']+59999)//60000)*60000
        resource=storage[platform]['resource_id']+'/blobServices/default'
        counters=query(resource,'Transactions',start,end,by_api=True)
        total=query(resource,'Transactions',start,end)
        for filename,value in [('azure-transactions.json',counters),('azure-transactions-total.json',total),('azure-egress.json',query(resource,'Egress',start,end)),('azure-ingress.json',query(resource,'Ingress',start,end))]:
            (dest/filename).write_text(json.dumps(sanitize(value),indent=2)+'\n')
        print(platform,'Azure counters exported',flush=True)

if __name__=='__main__':main()
