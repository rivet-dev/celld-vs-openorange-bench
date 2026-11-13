#!/usr/bin/env python3
"""Provision and validate 24 equally distributed Rivet partitions."""
import collections
import json
import re
import time
import uuid
from common import LOCAL, inventory, ssh

OPERATOR = 'RUST_LOG=error /opt/rivet-celld-bench/bin/rivet-engine --config /opt/rivet-celld-bench/operator.json storage partition '

def manifest():
    output = ssh(inventory()['nodes'][0], OPERATOR + 'show').stdout
    return json.loads(output[output.index('{'):])

def validate(data, node_count=6, tablet_count=24):
    partitions = data.get('tablets', data.get('partitions', []))
    assert tablet_count % node_count == 0
    assert len(partitions) == tablet_count, f'Expected {tablet_count} partitions, found {len(partitions)}'
    owners = collections.Counter(p['maintainer'] for p in partitions)
    assert len(owners) == node_count and set(owners.values()) == {tablet_count//node_count}, owners
    replicas = collections.Counter()
    for p in partitions:
        members = [str(uuid.UUID(bytes=bytes(m))) for m in p['replicaSet']['members']]
        assert len(set(members)) == 3 and p['maintainer'] in members
        replicas.update(members)
    assert set(replicas) == set(owners) and set(replicas.values()) == {tablet_count*3//node_count}, replicas
    ranges = sorted((int(p['hashRange']['start'], 16), int(p['hashRange']['end'], 16) if p['hashRange']['end'] is not None else 2**128) for p in partitions)
    assert ranges[0][0] == 0 and ranges[-1][1] == 2**128
    assert all(a[1] == b[0] for a,b in zip(ranges, ranges[1:]))
    return owners

def main():
    nodes = inventory()['nodes']
    workers = []
    for host in nodes:
        output = ssh(host, "rg -a 'selected storage worker identity' /var/log/rivet-cost/engine.log | tail -n 1").stdout
        if not output:
            output = ssh(host, "grep -a 'selected storage worker identity' /var/log/rivet-cost/engine.log | tail -n 1").stdout
        worker = re.findall(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', output)[0]
        workers.append(worker)
    assert len(set(workers)) == len(nodes)
    (LOCAL / 'workers.json').write_text(json.dumps(dict(zip((h['name'] for h in nodes), workers)), indent=2))
    data = manifest()
    if not data.get('tablets', data.get('partitions', [])):
        print(ssh(nodes[0], OPERATOR + 'provision --partitions 24 --workers ' + ' '.join(workers), timeout=180).stdout)
        data = manifest()
    # Startup can temporarily reassign maintainers while workers join. Restore the
    # provisioned rotating placement before any workload begins, then verify it.
    for partition in data.get('tablets',data.get('partitions',[])):
        owner=str(uuid.UUID(bytes=bytes(partition['replicaSet']['members'][0])))
        if partition['maintainer'] != owner:
            ssh(nodes[0], OPERATOR+'move '+partition.get('tabletId',partition.get('partitionId'))+' '+owner)
    data=manifest()
    print('Maintainers:', dict(validate(data,node_count=len(nodes))),flush=True)
    (LOCAL / 'placement.json').write_text(json.dumps(data, indent=2))

if __name__ == '__main__': main()
