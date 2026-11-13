#!/usr/bin/env python3
"""Mount the dedicated benchmark data disk; format only a blank attached LUN 0."""
import concurrent.futures
from common import inventory,ssh
COMMAND=r'''set -eu
sudo cloud-init status --wait >/dev/null
if mountpoint -q /var/lib/rivet; then
  echo 'Existing benchmark data mount retained'
  exit 0
fi
disk=/dev/disk/azure/data/by-lun/0
if [ ! -b "$disk" ]; then disk=/dev/disk/azure/scsi1/lun0; fi
[ -b "$disk" ] || { echo 'Missing benchmark LUN 0' >&2; exit 1; }
# Never format a disk with a filesystem or partition table.
if ! sudo blkid "$disk" >/dev/null 2>&1; then
  [ -z "$(sudo wipefs --noheadings --output TYPE "$disk")" ] || { echo 'Disk is not blank' >&2; exit 1; }
  sudo mkfs.ext4 -q "$disk"
fi
sudo mkdir -p /var/lib/rivet
uuid=$(sudo blkid -s UUID -o value "$disk")
[ -n "$uuid" ] || exit 1
if ! grep -q "UUID=$uuid " /etc/fstab; then
  printf 'UUID=%s /var/lib/rivet ext4 defaults,noatime,nofail 0 2\n' "$uuid" | sudo tee -a /etc/fstab >/dev/null
fi
sudo mount /var/lib/rivet
sudo chown "$(id -u):$(id -g)" /var/lib/rivet
'''
def prepare(host):
 print(host['name'],ssh(host,COMMAND,timeout=300).stdout.strip(),flush=True)
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:list(pool.map(prepare,inventory()['nodes']))
