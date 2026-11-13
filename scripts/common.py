"""Private inventory and SSH helpers shared by setup, runs and collection."""
import json
import os
import shlex
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local"


def inventory():
    return json.loads(Path(os.environ.get('BENCH_INVENTORY',LOCAL/'infra.json')).read_text())


def ssh(host, command, *, check=True, timeout=120, input=None):
    address = host["public_ip"] if isinstance(host, dict) else host
    args = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=15",
            "-o", "StrictHostKeyChecking=accept-new", "-o", f"UserKnownHostsFile={LOCAL / 'known_hosts'}",
            f"{inventory().get('admin_user', 'rivet')}@{address}", command]
    return subprocess.run(args, text=True, input=input, capture_output=True, check=check, timeout=timeout)


def upload(host, source, destination):
    address = host["public_ip"]
    transport = f"ssh -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile={LOCAL / 'known_hosts'}"
    subprocess.run(["rsync", "-az", "--partial", "-e", transport, str(source),
                    f"{inventory().get('admin_user', 'rivet')}@{address}:{destination}"], check=True)


def download_gzip(host, source, destination):
    """Stream a large event log without buffering it in the controller process."""
    args = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15',
            '-o', 'ServerAliveInterval=15', '-o', 'StrictHostKeyChecking=accept-new',
            '-o', f'UserKnownHostsFile={LOCAL / "known_hosts"}',
            f"{inventory().get('admin_user', 'rivet')}@{host['public_ip']}",
            'gzip -c -- ' + shlex.quote(source)]
    with destination.open('wb') as output:
        subprocess.run(args, stdout=output, check=True, timeout=300)
