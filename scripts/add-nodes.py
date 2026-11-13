#!/usr/bin/env python3
"""Add matching Azure benchmark nodes; write a separate expanded inventory."""
import argparse, concurrent.futures, json, subprocess
from common import LOCAL, inventory

def az(*args):
    output=subprocess.check_output(['az',*args,'--only-show-errors','-o','json'],text=True)
    return json.loads(output) if output.strip() else None

def main():
    p=argparse.ArgumentParser();p.add_argument('--nodes',type=int,default=12);p.add_argument('--output',default='.local/infra-expanded.json');p.add_argument('--size',help='VM size for added nodes; defaults to the existing nodes');a=p.parse_args()
    infra=inventory();rg=infra['resource_group'];base=infra['nodes'][0]
    assert len(infra['nodes'])<a.nodes<=24 and 24%a.nodes==0
    vm=az('vm','show','-g',rg,'-n',base['name'],'--query','{size:hardwareProfile.vmSize,zones:zones,image:storageProfile.imageReference,nic:networkProfile.networkInterfaces[0].id,keys:osProfile.linuxConfiguration.ssh.publicKeys,security:securityProfile.securityType}')
    target_size=a.size or vm['size']
    nic=az('network','nic','show','--ids',vm['nic'],'--query','{subnet:ipConfigurations[0].subnet.id,nsg:networkSecurityGroup.id}')
    image=vm['image'];urn=':'.join([image['publisher'],image['offer'],image['sku'],image.get('exactVersion',image['version'])])
    key=LOCAL/'scale-node.pub';key.write_text(vm['keys'][0]['keyData'])
    cloud=LOCAL/'scale-cloud-init.yaml';cloud.write_text('#cloud-config\npackage_update: true\npackages: [rsync, curl, jq, xz-utils, python3, prometheus-node-exporter]\n')
    existing={x['name']:x for x in az('vm','list','-g',rg,'--query','[].{name:name,tags:tags}')}
    def create(index):
        name=f'engine-{index+1}';tag='rivet-celld-cost-scale'
        if name in existing:
            assert existing[name].get('tags',{}).get('benchmark')==tag, f'Refusing to reuse unrelated VM {name}'
            assert az('vm','show','-g',rg,'-n',name,'--query','hardwareProfile.vmSize')==target_size, f'Existing {name} has a different VM size'
        else:
            az('network','public-ip','create','-g',rg,'-n',name+'-cost-ip','--location',infra['location'],'--sku','Standard','--allocation-method','Static','--zone',vm['zones'][0],'--tags','benchmark='+tag)
            args=['network','nic','create','-g',rg,'-n',name+'-cost-nic','--location',infra['location'],'--subnet',nic['subnet'],'--public-ip-address',name+'-cost-ip','--accelerated-networking','true','--tags','benchmark='+tag]
            if nic.get('nsg'):args+=['--network-security-group',nic['nsg']]
            az(*args)
            args=['vm','create','-g',rg,'-n',name,'--location',infra['location'],'--zone',vm['zones'][0],'--size',target_size,'--image',urn,'--admin-username',infra.get('admin_user','rivet'),'--ssh-key-values',str(key),'--nics',name+'-cost-nic','--os-disk-size-gb','64','--storage-sku','Premium_LRS','--custom-data',str(cloud),'--tags','benchmark='+tag]
            if vm.get('security'):args+=['--security-type',vm['security']]
            az(*args)
        disks=az('vm','show','-g',rg,'-n',name,'--query','storageProfile.dataDisks')
        if not disks:
            az('disk','create','-g',rg,'-n',name+'-cost-data','--location',infra['location'],'--zone',vm['zones'][0],'--size-gb','1024','--sku','PremiumV2_LRS','--disk-iops-read-write','20000','--disk-mbps-read-write','900','--tags','benchmark='+tag)
            az('vm','disk','attach','-g',rg,'--vm-name',name,'--name',name+'-cost-data','--lun','0','--caching','None')
        public=az('network','public-ip','show','-g',rg,'-n',name+'-cost-ip','--query','ipAddress')
        private=az('network','nic','show','-g',rg,'-n',name+'-cost-nic','--query','ipConfigurations[0].privateIPAddress')
        print(name+': matching VM and data disk ready',flush=True)
        return {'name':name,'public_ip':public,'private_ip':private,'size':target_size}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        added=list(pool.map(create,range(len(infra['nodes']),a.nodes)))
    from pathlib import Path
    Path(a.output).write_text(json.dumps({**infra,'nodes':infra['nodes']+added},indent=2)+'\n')
    print(f'Expanded inventory saved to {a.output}; active inventory unchanged.',flush=True)

if __name__=='__main__':main()
