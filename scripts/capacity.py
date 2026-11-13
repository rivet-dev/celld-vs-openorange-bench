#!/usr/bin/env python3
"""Record stored-byte snapshots between holds, outside measured request windows."""
import argparse,concurrent.futures,json,time
from common import ROOT,LOCAL

def inventory_sizes(storage,key,prefix,platform=None):
    from azure.storage.blob import BlobPrefix,ContainerClient
    # Stream pages instead of materializing every blob's metadata in the CLI.
    def client():
        return ContainerClient(f"https://{storage['account']}.blob.core.windows.net",storage['container'],credential=key,connection_timeout=30,read_timeout=120)
    def scan(path):
        stored_bytes=objects=0
        with client() as container:
            for blob in container.list_blobs(name_starts_with=path,results_per_page=5000):
                stored_bytes+=blob.size;objects+=1
        return stored_bytes,objects
    root=prefix+'/'
    if platform!='celld' or json.loads((ROOT/'versions.json').read_text())['celld']!='0.6.3':
        return scan(root)
    stored_bytes=objects=0;paths=[]
    with client() as container:
        for entry in container.walk_blobs(name_starts_with=root,delimiter='/',results_per_page=5000):
            if not isinstance(entry,BlobPrefix):
                stored_bytes+=entry.size;objects+=1
            elif entry.name!=root+'cells/':
                paths.append(entry.name)
            else:
                for cell in container.walk_blobs(name_starts_with=entry.name,delimiter=':',results_per_page=5000):
                    if not isinstance(cell,BlobPrefix):
                        stored_bytes+=cell.size;objects+=1
                    elif cell.name==root+'cells/LoadActor:':
                        # The pinned celld fixture's IDs are 64 lowercase hex digits:
                        # crates/celld/engine_api.rs::durable_object_id_hex at 8702f6d.
                        paths.extend(cell.name+digit for digit in '0123456789abcdef')
                    else:
                        paths.append(cell.name)
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        for size,count in pool.map(scan,paths):stored_bytes+=size;objects+=count
    return stored_bytes,objects

def snapshot(run,platform,level):
    storage=json.loads((LOCAL/'storage.json').read_text())[platform]
    key=json.loads((LOCAL/'storage-keys.json').read_text())[platform]
    prefix=(LOCAL/'object-prefix').read_text().strip()
    started=round(time.time()*1000)
    stored_bytes,objects=inventory_sizes(storage,key,prefix,platform)
    row={'platform':platform,'after_agents':level,'started_ms':started,'finished_ms':round(time.time()*1000),'stored_bytes':stored_bytes,'objects':objects,'method':'Azure Blob listing of the isolated benchmark prefix; outside measured holds'}
    dest=ROOT/'results'/run/platform/f'capacity-{level}.json';dest.write_text(json.dumps(row,indent=2)+'\n')
    print(platform,level,'stored bytes',row['stored_bytes'],flush=True)
    return row

def main():
    p=argparse.ArgumentParser();p.add_argument('run');p.add_argument('--watch',action='store_true');a=p.parse_args()
    if not a.watch: raise SystemExit('Use --watch while the sweep runs; inventory must be outside measured holds.')
    done=set()
    while len(done)<16:
        for platform in ['rivet','celld']:
            path=ROOT/'results'/a.run/platform/'run.json'
            if not path.exists():continue
            try:meta=json.loads(path.read_text())
            except json.JSONDecodeError:continue
            for stage in meta['stages']:
                level=stage['actors'];ident=(platform,level)
                if ident in done:continue
                target=path.parent/f'capacity-{level}.json'
                if target.exists():done.add(ident);continue
                # The next stage cannot measure until its >=60s settling period.
                # A late watcher must not invent a historical capacity snapshot.
                if time.time()*1000-stage['end_ms']>25000:
                    raise RuntimeError(f'Missed between-hold inventory window: {ident}')
                snapshot(a.run,platform,level);done.add(ident)
        time.sleep(1)

if __name__=='__main__':main()
