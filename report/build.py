#!/usr/bin/env python3
"""Regenerate the comparison entirely from the archived counters and driver samples."""
import argparse,collections,csv,datetime,gzip,html,json,re,statistics,sys
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter,MaxNLocator

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from measurements import completed_levels
PRICES=json.loads((ROOT/'pricing.json').read_text())
MONTH_MINUTES=PRICES['hours_per_month']*60
READ={'GetBlob','GetBlobProperties','GetBlobMetadata','GetBlockList','GetContainerProperties','GetContainerMetadata','GetBlobServiceProperties'}
WRITE={'PutBlob','PutBlock','PutBlockList','ListBlobs','SetBlobProperties','SetBlobMetadata','CreateContainer','AcquireBlobLease','RenewBlobLease','ReleaseBlobLease'}
FREE={'DeleteBlob','BatchDeleteBlob','BatchRequest','DeleteContainer'}

def ms(text): return round(datetime.datetime.fromisoformat(text.replace('Z','+00:00')).timestamp()*1000)
def load(path): return json.loads(path.read_text())
def ndjson(path):
    with (gzip.open(path,'rt') if path.suffix=='.gz' else path.open()) as f:
        return [json.loads(line) for line in f if line.strip()]
def cost(counts):
    unknown=set(counts)-READ-WRITE-FREE
    assert not unknown,f'Unclassified Azure APIs: {unknown}; add an explicit documented rate mapping'
    return sum(v*(PRICES['get_head_per_1000'] if k in READ else PRICES['put_list_per_1000'] if k in WRITE else 0)/1000 for k,v in counts.items())
def series(path):
    result=collections.defaultdict(dict)
    for s in load(path)['metrics'][0]['timeseries']:
        name=next((m['value'] for m in s.get('metadatavalues',[]) if m['name']['value'].lower()=='apiname'),'total')
        for d in s['data']: result[ms(d['timeStamp'])][name]=d.get('total')
    return result

def read_platform(base,platform):
    dest=base/platform;meta=load(dest/'run.json');accepted=completed_levels(meta)
    churn=meta.get('workload',{}).get('scenario')=='churn'
    counters=series(dest/'azure-transactions.json');totals=series(dest/'azure-transactions-total.json')
    samples=[ndjson(p) for p in sorted(dest.glob('shard-*-samples.ndjson'))]
    if not meta['complete']:
        samples=[[s for s in shard if s['timestamp_ms']<meta['stages'][-1]['end_ms']] for shard in samples]
    event_paths=sorted(dest.glob('shard-*-events.ndjson*'))
    events=[e for p in event_paths for e in ndjson(p)]
    shards=meta.get('loadgen_count',2);nodes=meta.get('node_count',meta.get('workload',{}).get('nodes',6))
    assert len(samples)==shards and len(event_paths)==shards, 'Missing driver files'
    rows=[];minutes=[]
    for stage in meta['stages']:
        start,end=stage['start_ms'],stage['end_ms'];assert end-start==300000
        count=stage['actors'];per_min=[];all_counts=collections.Counter()
        for stamp in range(start,end,60000):
            total=totals.get(stamp,{}).get('total')
            assert total is not None and total>0,f'Missing aggregate telemetry: {platform} {stamp}'
            values={api:int(value or 0) for api,value in counters.get(stamp,{}).items()}
            assert sum(values.values())==int(total),f'API totals do not reconcile: {platform} {stamp}'
            dollars=cost(values);per_min.append(dollars);all_counts.update(values)
            minutes.append({'platform':platform,'timestamp_ms':stamp,'actors':count,'cost_usd':dollars,'monthly_usd':dollars*MONTH_MINUTES,'requests':values})
        mean_connected=0
        for shard in samples:
            selected=[s for s in shard if start<=s['timestamp_ms']<end]
            assert len(selected)>=298, 'Missing held-population samples'
            assert all(s['errors']==0 and s['connect_errors']==0 for s in selected),'Client errors during hold'
            mean_connected+=statistics.mean(s['connected'] for s in selected)
            if churn:
                assert all(s['ready_slots']==count//shards and s['connected']+s['opening']+s.get('retrying',0)==count//shards for s in selected),'Missing churn slots'
                assert statistics.mean(s['connected'] for s in selected)>=count/shards*.99,'Churn population below target'
            else:
                assert all(s['connected']==count//shards for s in selected),'Invalid hold'
        lifecycle={}
        if churn:
            completed=[e for e in events if e['type']=='retired' and start<=e['timestamp_ms']<end]
            assert len(completed)>=count*4.8,'Missing complete one-minute lifetimes'
            assert len({e['actor'] for e in completed})==len(completed),'Retired agent reused'
            assert all(59999<=e['lifetime_ms']<=61000 for e in completed),'Agent lifetime differs from one minute'
            assert all(e['close_latency_ms']<15000 for e in completed),'Retirement did not complete'
            lifecycle={'mean_connected':mean_connected,'completed_lifetimes':len(completed),'lifetime_min_ms':min(e['lifetime_ms'] for e in completed),'lifetime_max_ms':max(e['lifetime_ms'] for e in completed),'created_total':sum(e['type']=='connected' and e['timestamp_ms']<end for e in events)}
        writes=[e for e in events if e['type']=='write' and start<=e['timestamp_ms']<end]
        assert writes
        capacity_path=dest/f'capacity-{count}.json'
        assert capacity_path.exists(), f'Missing stored-byte snapshot: {capacity_path}'
        capacity=load(capacity_path)
        capacity_status='measured_after_hold'
        if capacity:
            assert capacity['started_ms'] >= end
            next_population_changes=[e['timestamp_ms'] for e in events if e['type']=='stage' and e['level']>count]
            assert all(not (s['start_ms']<=capacity['started_ms']<s['end_ms'] or s['start_ms']<capacity['finished_ms']<=s['end_ms']) for s in meta['stages']), 'Inventory overlapped a measured hold'
            if next_population_changes and capacity['finished_ms']>=min(next_population_changes):
                capacity_status='inventory_crossed_population_change'
                capacity=None
        rows.append({'storage_sample_status':capacity_status,'stored_bytes':capacity['stored_bytes'] if capacity else None,'storage_monthly_usd':capacity['stored_bytes']/1e12*PRICES['storage_per_decimal_tb_month'] if capacity else None,'platform':platform,'actors':count,'start_ms':start,'end_ms':end,'writes':len(writes),'errors':0,'cost_usd':sum(per_min),'monthly_usd':statistics.mean(per_min)*MONTH_MINUTES,'monthly_min_usd':min(per_min)*MONTH_MINUTES,'monthly_max_usd':max(per_min)*MONTH_MINUTES,'requests':dict(all_counts)})
        rows[-1].update(lifecycle)
        rows[-1]['connection_retries']=sum(e['type']=='connect_retry' and start<=e['timestamp_ms']<end for e in events)
        rows[-1].update(node_count=stage.get('node_count',nodes),source_run=base.name)
        rows[-1]['node_sizes']=meta.get('node_sizes',{'Standard_D16as_v7':nodes})
        rows[-1]['stored_objects']=capacity.get('objects') if capacity else None
    assert [r['actors'] for r in rows]==accepted, 'Incomplete selected holds'
    return {'metadata':meta,'rows':rows,'minutes':minutes,'samples':samples,'counters':counters,'totals':totals}

def read_campaign(base):
    """Combine complete sweeps while retaining their real timestamps and topology."""
    runs=load(base/'campaign.json')['runs']
    data={}
    for platform in ['rivet','celld']:
        segments=[read_platform(ROOT/'results'/run,platform) for run in runs]
        first=segments[0]
        workload_keys=['scenario','lifetime_ms','write_interval_ms','write_jitter_fraction','payload_bytes','rows_per_actor','retirement','replacement']
        expected={key:first['metadata']['workload'].get(key) for key in workload_keys}
        assert all({key:s['metadata']['workload'].get(key) for key in workload_keys}==expected for s in segments), 'Campaign workloads differ'
        rows=[r for segment in segments for r in segment['rows']]
        assert [r['actors'] for r in rows]==sorted(set(r['actors'] for r in rows)), 'Overlapping or unordered campaign levels'
        data[platform]={**first,'rows':rows,'segments':segments,
            'minutes':[m for s in segments for m in s['minutes']],
            'counters':{t:v for s in segments for t,v in s['counters'].items()},
            'totals':{t:v for s in segments for t,v in s['totals'].items()}}
    return data

def combined_population(shards,field='connected'):
    # Each driver samples on its own clock cadence. Carry its most recent
    # observation forward instead of turning an absent time bucket into zero.
    observations=sorted((s['timestamp_ms'],i,s[field]) for i,shard in enumerate(shards) for s in shard)
    latest={};times=[];counts=[]
    for stamp,shard,count in observations:
        latest[shard]=count
        if len(latest)==len(shards):
            times.append(stamp);counts.append(sum(latest.values()))
    return times,counts


# Chart colors follow the report page's dark tokens so the static SVGs match it.
COLORS={'rivet':'#3987e5','celld':'#d95926'}
LABELS={'rivet':'Rivet Actors','celld':'celld Durable Objects'}
SHORT={'rivet':'Rivet','celld':'celld'}
PLATFORM_ORDER=['celld','rivet']
# Cost groups in stacking order. Every API maps to exactly one group through its
# documented price class, so the groups always sum to the measured total.
GROUPS=[('PutBlob','PutBlob','put','#9085e9'),('ListBlobs','ListBlobs','list','#d55181'),('GetBlob','GetBlob','get','#c98500'),('other','Other billed','other','#898781'),('free','Deletes (free)','free','#52514e')]
NAMED_GROUPS={'PutBlob','ListBlobs','GetBlob'}

def group_key(api):
    if api in FREE:return 'free'
    if api in NAMED_GROUPS:return api
    return 'other'

def grouped(requests):
    """Split one measurement's API counts into cost groups with their S3-priced dollars."""
    result={key:{'count':0,'usd':0.0,'apis':{}} for key,*_ in GROUPS}
    for api,count in requests.items():
        if not count:continue
        group=result[group_key(api)]
        group['count']+=count;group['usd']+=cost({api:count});group['apis'][api]=count
    return result

def theme():
    plt.rcParams.update({'figure.facecolor':'#1a1a19','axes.facecolor':'#1a1a19','savefig.facecolor':'#1a1a19','text.color':'#f0efec','axes.labelcolor':'#c3c2b7','xtick.color':'#898781','ytick.color':'#898781','axes.edgecolor':'#383835','grid.color':'#2c2c2a','font.family':'DejaVu Sans','font.size':10,'svg.fonttype':'none'})

def style(ax,ylabel):
    ax.set_ylabel(ylabel);ax.grid(alpha=.9);ax.spines[['top','right']].set_visible(False);ax.set_ylim(bottom=0)

def dollars(ax):
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v,_:f'${v:,.0f}'))

def plot(data,out):
    """Static copies of the page's main charts for the README and offline viewing."""
    theme()
    churn=data['rivet']['metadata'].get('workload',{}).get('scenario')=='churn'
    xmax=max(r['actors'] for d in data.values() for r in d['rows'])*1.04
    fig=plt.figure(figsize=(12,9),layout='constrained')
    grid=fig.add_gridspec(2,2,height_ratios=[1.1,1])
    top=fig.add_subplot(grid[0,:])
    for p in PLATFORM_ORDER:
        rows=data[p]['rows']
        top.plot([r['actors'] for r in rows],[r['monthly_usd'] for r in rows],color=COLORS[p],marker='o',linewidth=2,label=LABELS[p])
        top.annotate(f"{SHORT[p]} ${rows[-1]['monthly_usd']:,.0f}/mo",(rows[-1]['actors'],rows[-1]['monthly_usd']),xytext=(8,0),textcoords='offset points',va='center',color='#f0efec')
    style(top,'Monthly request cost (USD)');dollars(top);top.legend(frameon=False,loc='upper left');top.set_xlim(0,xmax*1.08)
    top.set_title('Monthly object-storage request cost vs concurrent agents',loc='left',fontsize=15,pad=12)
    shared=None
    for column,p in enumerate(PLATFORM_ORDER):
        ax=fig.add_subplot(grid[1,column],sharey=shared)
        shared=shared or ax
        rows=data[p]['rows'];x=[r['actors'] for r in rows]
        layers=[];labels=[];colors=[]
        for key,label,_,color in GROUPS:
            values=[grouped(r['requests'])[key]['usd']/5*MONTH_MINUTES for r in rows]
            if any(values):layers.append(values);labels.append(label);colors.append(color)
        ax.stackplot(x,layers,labels=labels,colors=colors,alpha=.75,edgecolor='#1a1a19',linewidth=.6)
        style(ax,'Monthly request cost (USD)');dollars(ax);ax.set_xlim(0,xmax)
        ax.set_title(f'{LABELS[p]}: cost by Azure API (same y-axis)',loc='left',fontsize=12)
        ax.set_xlabel('Concurrent agents (linear)' if churn else 'Held agents (linear)')
        ax.legend(frameon=False,loc='upper left',fontsize=9)
    fig.savefig(out/'scaling.svg');plt.close(fig)

    fig,axes=plt.subplots(4 if churn else 3,1,figsize=(12,12 if churn else 10),sharex=True,layout='constrained')
    request_axis=axes[-2];cost_axis=axes[-1]
    for p in PLATFORM_ORDER:
        d=data[p]
        origin=d['metadata']['stages'][0]['start_ms']
        for index,segment in enumerate(d.get('segments',[d])):
            times,counts=combined_population(segment['samples'])
            axes[0].step([(t-origin)/60000 for t in times],counts,where='post',color=COLORS[p],label=LABELS[p] if index==0 else None,linewidth=1.5)
            if churn:
                retired_times,retired_counts=combined_population(segment['samples'],'retired')
                axes[1].plot([(t-origin)/60000 for t in retired_times],retired_counts,color=COLORS[p])
        stamps=list(range(origin,max(d['counters'])+1,60000))
        amounts=[{k:int(v or 0) for k,v in d['counters'].get(t,{}).items()} for t in stamps]
        measured={m['timestamp_ms'] for m in d['minutes']}
        request_axis.plot([(t-origin)/60000 for t in stamps],[sum(v.values()) if t in measured else float('nan') for t,v in zip(stamps,amounts)],color=COLORS[p],marker='.',label=LABELS[p])
        cost_axis.plot([(t-origin)/60000 for t in stamps],[cost(v)*MONTH_MINUTES if t in measured else float('nan') for t,v in zip(stamps,amounts)],color=COLORS[p],marker='.',label=LABELS[p])
    labels=['Actual connected agents']+(['Completed one-minute lifetimes'] if churn else [])+['Actual Azure requests / minute','Monthly request cost (USD)']
    for ax,label in zip(axes,labels):style(ax,label)
    axes[0].legend(frameon=False);cost_axis.set_xlabel('Minutes since the first measured hold (each platform’s own run)')
    dollars(cost_axis)
    axes[0].set_title('Recorded agents and measured request rates over time',loc='left',fontsize=15,pad=16)
    fig.savefig(out/'timeline.svg');plt.close(fig)

def money(v):return f'${v:,.0f}' if v>=100 else f'${v:,.2f}'
def storage_money(v):return '—' if v is None else f'${v:.4f}' if v<0.01 else f'${v:.2f}'
def per_lifetime(count,lifetimes):
    value=count/lifetimes
    if value==0:return '0'
    return f'{value:.2f}' if value>=0.1 else f'{value:.3f}'
def pct(part,whole):return f'{part/whole*100:.0f}%' if whole else '—'
def esc(value):return html.escape(str(value))

def level_entry(requests,minutes,extra):
    groups=grouped(requests)
    entry={'groups':{key:{'per_min':g['count']/minutes,'monthly':g['usd']/minutes*MONTH_MINUTES} for key,g in groups.items()},
           'req_per_min':sum(requests.values())/minutes}
    entry.update(extra)
    return entry

def payload(data):
    rivet_rows=data['rivet']['rows']
    levels=[]
    for index,row in enumerate(rivet_rows):
        level={'agents':row['actors'],'nodes':row['node_count']}
        for p in PLATFORM_ORDER:
            r=data[p]['rows'][index]
            level[p]=level_entry(r['requests'],5,{'monthly':r['monthly_usd'],'monthly_min':r['monthly_min_usd'],'monthly_max':r['monthly_max_usd']})
        levels.append(level)
    minutes={}
    for p in PLATFORM_ORDER:
        minutes[p]=[level_entry(m['requests'],1,{'agents':m['actors'],'monthly':m['monthly_usd'],'req':sum(m['requests'].values()),
            'utc':datetime.datetime.fromtimestamp(m['timestamp_ms']/1000,datetime.timezone.utc).strftime('%Y-%m-%d %H:%M')}) for m in data[p]['minutes']]
    return {'xmax':max(r['actors'] for r in rivet_rows)*1.04,
            'platforms':[{'key':p,'label':LABELS[p],'short':SHORT[p]} for p in PLATFORM_ORDER],
            'groups':[{'key':key,'label':label,'color':color} for key,label,color,_ in GROUPS],
            'levels':levels,'minutes':minutes}

def rate_text(apis):
    classes=[]
    if any(api in WRITE for api in apis):classes.append(f"${PRICES['put_list_per_1000']}")
    if any(api in READ for api in apis):classes.append(f"${PRICES['get_head_per_1000']}")
    return ' or '.join(classes)+' / 1k' if classes else 'free'

def lifetime_table(data):
    """Per-API arithmetic at the top level: count per lifetime, monthly dollars and share."""
    top={p:data[p]['rows'][-1] for p in PLATFORM_ORDER}
    groups={p:grouped(top[p]['requests']) for p in PLATFORM_ORDER}
    head=('<thead><tr><th></th><th></th>'+''.join(f'<th colspan="3">{esc(LABELS[p])}</th>' for p in PLATFORM_ORDER)+'</tr>'
          '<tr><th>Azure API</th><th>S3 rate</th>'+'<th>Per lifetime</th><th>Cost / month</th><th>Share</th>'*len(PLATFORM_ORDER)+'</tr></thead>')
    body=''
    for key,label,_,_ in GROUPS:
        apis=sorted(set().union(*(groups[p][key]['apis'] for p in PLATFORM_ORDER)))
        if not apis:continue
        name=esc(label) if key in NAMED_GROUPS else f"{esc(label)}<div class=\"note\">{esc(', '.join(apis))}</div>"
        cells=''
        for p in PLATFORM_ORDER:
            g=groups[p][key];row=top[p]
            monthly=g['usd']/5*MONTH_MINUTES
            cells+=f"<td>{per_lifetime(g['count'],row['completed_lifetimes'])}</td><td>{money(monthly)}</td><td>{pct(g['usd'],row['cost_usd'])}</td>"
        body+=f'<tr><td>{name}</td><td>{esc(rate_text(apis))}</td>{cells}</tr>'
    totals=''.join(f"<td>{per_lifetime(sum(top[p]['requests'].values()),top[p]['completed_lifetimes'])}</td><td>{money(top[p]['monthly_usd'])}</td><td>100%</td>" for p in PLATFORM_ORDER)
    body+=f'<tr class="total"><td>Total</td><td></td>{totals}</tr>'
    return head+'<tbody>'+body+'</tbody>'

def share_bars(data):
    out=''
    for p in PLATFORM_ORDER:
        row=data[p]['rows'][-1];groups=grouped(row['requests'])
        blocks=''
        for key,label,color,_ in GROUPS:
            usd=groups[key]['usd']
            if usd<=0:continue
            share=usd/row['cost_usd']
            ink='var(--on-get)' if color=='get' else '#fff'
            inner=f'{esc(label)} {share*100:.0f}%' if share>=0.12 else (f'{share*100:.0f}%' if share>=0.04 else '')
            blocks+=f'<div style="flex:{share:.6f} 1 0;background:var(--{color});color:{ink}" title="{esc(label)}: {money(usd/5*MONTH_MINUTES)}/month, {share*100:.1f}%">{inner}</div>'
        out+=f'<div><div class="name"><span>{esc(LABELS[p])}</span><span>{money(row["monthly_usd"])}/month</span></div><div class="bar" role="img" aria-label="{esc(LABELS[p])} cost share by API">{blocks}</div></div>'
    return out

def raw_tables(data,apis):
    get_apis=[a for a in apis if a in READ];put_apis=[a for a in apis if a in WRITE];free_apis=[a for a in apis if a in FREE]
    churn=data['rivet']['metadata'].get('workload',{}).get('scenario')=='churn'
    lead=['Agents','Nodes','Request cost / month','1-minute range']
    tail=(['Requests per lifetime'] if churn else [])+['SQLite writes']+(['Lifetimes'] if churn else [])+['Stored GB','Storage / month']
    groups_row='<tr>'+f'<th colspan="{len(lead)}"></th>'
    for title,members in [(f"GET/HEAD · ${PRICES['get_head_per_1000']} / 1k",get_apis),(f"PUT/LIST · ${PRICES['put_list_per_1000']} / 1k",put_apis),('Deletes · free',free_apis)]:
        if members:groups_row+=f'<th colspan="{len(members)}">{esc(title)}</th>'
    groups_row+=f'<th colspan="{len(tail)}"></th></tr>'
    names_row='<tr>'+''.join(f'<th>{esc(k)}</th>' for k in lead+get_apis+put_apis+free_apis+tail)+'</tr>'
    out=''
    for p in PLATFORM_ORDER:
        body=''
        for r in data[p]['rows']:
            cells=[f"{r['actors']:,}",str(r['node_count']),f"<strong>{money(r['monthly_usd'])}</strong>",f"{money(r['monthly_min_usd'])}–{money(r['monthly_max_usd'])}"]
            counts=''.join(f'<td class="dim">0</td>' if not r['requests'].get(a,0) else f"<td>{r['requests'][a]:,}</td>" for a in get_apis+put_apis+free_apis)
            rest=([per_lifetime(sum(r['requests'].values()),r['completed_lifetimes'])] if churn else [])+[f"{r['writes']:,}"]+([f"{r['completed_lifetimes']:,}"] if churn else [])+['—' if r['stored_bytes'] is None else f"{r['stored_bytes']/1e9:.2f}",storage_money(r['storage_monthly_usd'])]
            body+='<tr>'+''.join(f'<td>{c}</td>' for c in cells)+counts+''.join(f'<td>{c}</td>' for c in rest)+'</tr>'
        out+=f'<h3 style="margin-top:22px">{esc(LABELS[p])}: every five-minute hold</h3><div class="table"><table><thead>{groups_row}{names_row}</thead><tbody>{body}</tbody></table></div>'
    return out

def fill(template,values):
    markers=set(re.findall(r'%%([A-Z_]+)%%',template))
    assert markers==set(values),f'Template markers differ: {sorted(markers^set(values))}'
    return re.sub(r'%%([A-Z_]+)%%',lambda match:values[match.group(1)],template)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('run');args=parser.parse_args()
    base=ROOT/'results'/args.run;out=ROOT/'report'/args.run;out.mkdir(parents=True,exist_ok=True)
    campaign=(base/'campaign.json').exists()
    data=read_campaign(base) if campaign else {p:read_platform(base,p) for p in ['rivet','celld']}
    assert [(r['actors'],r['node_count'],r['node_sizes']) for r in data['rivet']['rows']]==[(r['actors'],r['node_count'],r['node_sizes']) for r in data['celld']['rows']], 'Both platforms must use matching levels and hardware'
    churn=data['rivet']['metadata'].get('workload',{}).get('scenario')=='churn'
    assert churn==(data['celld']['metadata'].get('workload',{}).get('scenario')=='churn')
    plot(data,out)
    rows=[r for d in data.values() for r in d['rows']]
    (out/'summary.json').write_text(json.dumps({'pricing':PRICES,'results':rows},indent=2)+'\n')
    priority=['GetBlob','PutBlob','ListBlobs','GetBlobProperties','DeleteBlob','BatchDeleteBlob','BatchRequest']
    apis=sorted(set().union(*(r['requests'] for r in rows)),key=lambda api:(priority.index(api) if api in priority else len(priority),api))
    with (out/'requests.csv').open('w') as f:
        w=csv.writer(f);w.writerow(['platform','agents','measured_seconds','acknowledged_sqlite_writes',*apis,'measured_request_cost_usd','monthly_request_cost_usd','stored_bytes','storage_monthly_cost_usd','stored_objects','nodes','source_run'])
        for r in rows:w.writerow([r['platform'],r['actors'],300,r['writes'],*[r['requests'].get(k,0) for k in apis],r['cost_usd'],r['monthly_usd'],r['stored_bytes'],r['storage_monthly_usd'],r['stored_objects'],r['node_count'],r['source_run']])
    with (out/'minutes.csv').open('w') as f:
        w=csv.writer(f);w.writerow(['platform','timestamp_utc','held_agents',*apis,'request_cost_usd','monthly_equivalent_usd'])
        for d in data.values():
            for m in d['minutes']:w.writerow([m['platform'],datetime.datetime.fromtimestamp(m['timestamp_ms']/1000,datetime.timezone.utc).isoformat(),m['actors'],*[m['requests'].get(k,0) for k in apis],m['cost_usd'],m['monthly_usd']])
    if churn:
        with (out/'lifetimes.csv').open('w') as f:
            w=csv.writer(f);fields=['platform','actors','mean_connected','completed_lifetimes','lifetime_min_ms','lifetime_max_ms','created_total','connection_retries'];w.writerow(fields)
            for r in rows:w.writerow([r[k] for k in fields])

    # Page text is computed from the measured rows so a rebuild never carries stale numbers.
    agents_word='concurrent agents' if churn else 'held agents'
    rivet_rows=data['rivet']['rows'];celld_rows=data['celld']['rows']
    top_rivet=rivet_rows[-1];top_celld=celld_rows[-1]
    ratio=top_celld['monthly_usd']/top_rivet['monthly_usd']
    v=load(ROOT/'versions.json')
    groups_by_nodes=collections.defaultdict(list)
    for r in rivet_rows:groups_by_nodes[r['node_count']].append(r['actors'])
    topology=', '.join(f"{nodes} nodes at {min(c):,}" + (f"–{max(c):,}" if max(c)!=min(c) else '') for nodes,c in groups_by_nodes.items())
    one_worker=any(s['metadata']['workload'].get('rivet_tokio_worker_threads')==1 for s in data['rivet'].get('segments',[data['rivet']]))
    max_storage=max(r['storage_monthly_usd'] or 0 for r in rows)

    if churn:
        workload='Each agent stays awake <strong>60 s</strong>, makes <strong>one SQLite write every 10 s</strong>, then sleeps with its data kept and is replaced by a fresh agent. Each level settles 120 s, then 5 minutes are measured.'
    else:
        workload='Agents stay awake on an open WebSocket and write <strong>one SQLite write every 10 s</strong>. Each level settles 60 s, then 5 minutes are measured.'
    tiles=''
    for p,row in [('celld',top_celld),('rivet',top_rivet)]:
        tiles+=(f'<div class="tile"><div class="label"><span class="key" style="background:var(--{p})"></span>{esc(LABELS[p])}</div>'
                f'<div class="value">{money(row["monthly_usd"])}<span class="sub"> / month</span></div></div>')
    tiles+=f'<div class="tile"><div class="label">celld ÷ Rivet</div><div class="value">{ratio:.1f}×</div></div>'
    tiles=f'<p class="tiles-title">At {top_rivet["actors"]:,} {agents_word}</p>'+tiles

    base_nodes=rivet_rows[0]['node_count']
    same_nodes=[r for r in rivet_rows if r['node_count']==base_nodes]
    low=next((r for r in same_nodes if r['actors']>=1000),same_nodes[0]);high=same_nodes[-1]
    look_scaling=(f"celld rises in a straight line, about <strong>${top_celld['monthly_usd']/top_celld['actors']:.2f} per agent per month</strong>. "
                  f"Rivet stays between {money(low['monthly_usd'])} and {money(high['monthly_usd'])} from {low['actors']:,} to {high['actors']:,} agents on {base_nodes} nodes and rises mostly where nodes are added.")
    look_breakdown="celld is almost all <strong>PutBlob + ListBlobs</strong>, the expensive PUT/LIST rate. Rivet is mostly <strong>PutBlob + GetBlob</strong>, and GetBlob is billed at the cheap GET rate."
    if churn:
        large=[r for r in celld_rows if r['actors']>=5000]
        def spread(api):
            values=[r['requests'].get(api,0)/r['completed_lifetimes'] for r in large]
            low_value,high_value=min(values),max(values)
            return f'{low_value:.1f}' if f'{low_value:.1f}'==f'{high_value:.1f}' else f'{low_value:.1f}–{high_value:.1f}'
        look_breakdown=("celld is almost all <strong>PutBlob + ListBlobs</strong> at the expensive PUT/LIST rate: "
                        f"{spread('PutBlob')} PutBlob and {spread('ListBlobs')} ListBlobs per agent lifetime at every level from {large[0]['actors']:,} up. "
                        "Rivet is a sliver on the same y-axis; the bars below show its split, mostly <strong>PutBlob + GetBlob</strong>.")
    rivet_peak=max(m['monthly_usd'] for m in data['rivet']['minutes'])
    look_minutes=(f"Raw one-minute Azure counters, five per level, on the same y-axis. Rivet's most expensive single minute "
                  f"({money(rivet_peak)}/month equivalent) barely leaves the baseline next to celld's {money(top_celld['monthly_usd'])} at {top_celld['actors']:,} agents.")

    scaling_rows=''.join(f"<tr><td>{a['actors']:,}</td><td>{a['node_count']}</td><td>{money(b['monthly_usd'])}</td><td>{money(a['monthly_usd'])}</td><td>{b['monthly_usd']/a['monthly_usd']:.1f}×</td><td>{sum(b['requests'].values())/5:,.0f}</td><td>{sum(a['requests'].values())/5:,.0f}</td></tr>" for a,b in zip(rivet_rows,celld_rows))
    scaling_table=f"<thead><tr><th>{esc(agents_word.capitalize())}</th><th>Nodes</th><th>celld cost / month</th><th>Rivet cost / month</th><th>celld ÷ Rivet</th><th>celld requests / min</th><th>Rivet requests / min</th></tr></thead><tbody>{scaling_rows}</tbody>"
    nonzero=[a for a in apis if any(r['requests'].get(a,0) for r in rows)]
    api_legend=''.join(f'<li><span class="swatch" style="background:var(--{color})"></span>{esc(label)}</li>' for key,label,color,_ in GROUPS if any(grouped(r['requests'])[key]['count'] for r in rows))
    lifetime_section=f"<h3>Requests and cost per agent lifetime at {top_rivet['actors']:,} agents</h3><div class=\"table\"><table>{lifetime_table(data)}</table></div>" if churn else ''
    capacity_note='<p class="note">— means the stored-byte inventory crossed a population change, so no storage estimate is assigned to that level.</p>' if any(r['stored_bytes'] is None for r in rows) else ''
    lifecycle=''
    if churn:
        body=''.join(f"<tr><td>{r['actors']:,}</td><td>{esc(LABELS[r['platform']])}</td><td>{r['mean_connected']:,.2f}</td><td>{r['completed_lifetimes']:,}</td><td>{r['lifetime_min_ms']/1000:.3f}–{r['lifetime_max_ms']/1000:.3f}</td><td>{r['created_total']:,}</td><td>{r['connection_retries']:,}</td></tr>" for r in rows)
        lifecycle=('<h3>Recorded one-minute lifetimes</h3><div class="table"><table><thead><tr><th>Target concurrency</th><th>Platform</th><th>Mean connected</th><th>Completed in five minutes</th><th>Lifetime range (s)</th><th>Agents created by end</th><th>Connection retries</th></tr></thead><tbody>'+body+'</tbody></table></div>')
    timeline='<h3>Full run timeline</h3><img src="timeline.svg" alt="Connected agents, completed lifetimes, Azure requests and repriced cost over elapsed time" style="width:100%;height:auto;border-radius:12px">'
    footnote=[f"Measured Azure Blob requests priced at S3 rates (GET/HEAD ${PRICES['get_head_per_1000']}, PUT/LIST ${PRICES['put_list_per_1000']} per 1,000, deletes free), projected to {PRICES['hours_per_month']} hours. Not an AWS or Azure bill. Compute, egress and storage are excluded; stored-byte snapshots price at {storage_money(max_storage)}/month or less.",
              f"Both platforms ran on the same Azure machines at each level ({esc(topology)}; 16 vCPU, 64 GB each). Rivet keeps 3 replicas of every write. Rivet Engine {esc(v['rivet_engine'])}, celld {esc(v['celld'])}."]
    if one_worker:footnote.append(f"At {top_rivet['actors']:,} agents each Rivet Engine ran with <code>TOKIO_WORKER_THREADS=1</code> to avoid a reply-admission race in the pinned release; earlier levels use the default.")
    data_json=json.dumps(payload(data),separators=(',',':')).replace('</','<\\/')
    template=(ROOT/'report'/'template.html').read_text()
    content=fill(template,{
        'TITLE':'Rivet vs celld storage request cost',
        'WORKLOAD':workload,'TILES':tiles,
        'LOOK_SCALING':look_scaling,'LOOK_BREAKDOWN':look_breakdown,'LOOK_MINUTES':look_minutes,
        'RIVET_LABEL':esc(LABELS['rivet']),'CELLD_LABEL':esc(LABELS['celld']),
        'API_LEGEND':api_legend,
        'SHARE_TITLE':f"Share of cost at {top_rivet['actors']:,} {agents_word}",
        'SHARE_BARS':share_bars(data),
        'SCALING_TABLE':scaling_table,'LIFETIME_SECTION':lifetime_section,
        'RAW_TABLES':raw_tables(data,nonzero),'CAPACITY_NOTE':capacity_note,
        'LIFECYCLE':lifecycle,'TIMELINE':timeline,
        'FOOTNOTE':''.join(f'<p>{line}</p>' for line in footnote),
        'DATA':data_json})
    (out/'index.html').write_text(content)
    print(out/'index.html')

if __name__=='__main__':main()
