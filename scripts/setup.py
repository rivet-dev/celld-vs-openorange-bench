#!/usr/bin/env python3
"""Install pinned binaries and isolated benchmark services on the common hosts."""
import concurrent.futures
import argparse
import copy
import hashlib
import json
import os
import secrets
import subprocess
from pathlib import Path

from common import ROOT, LOCAL, inventory, ssh, upload

REMOTE = "/opt/rivet-celld-bench"


def secret_file(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)


def unit(name, command, environment, working=REMOTE):
    return f"""[Unit]
Description=Storage cost benchmark {name}
After=network-online.target
[Service]
User={inventory().get("admin_user", "rivet")}
WorkingDirectory={working}
EnvironmentFile={REMOTE}/{environment}
ExecStart={command}
LimitNOFILE=1048576
Restart=no
TimeoutStopSec=90
StandardOutput=append:/var/log/rivet-cost/{name}.log
StandardError=append:/var/log/rivet-cost/{name}.log
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scenario', choices=['held', 'churn'], default='held')
    parser.add_argument('--hosts',nargs='+',help='Install only these inventory hosts')
    args = parser.parse_args()
    infra = inventory()
    storage = json.loads((LOCAL / "storage.json").read_text())
    keys = json.loads((LOCAL / "storage-keys.json").read_text())
    token_file = LOCAL / "admin-token"
    if not token_file.exists(): secret_file(token_file, secrets.token_hex(24))
    token = token_file.read_text().strip()
    prefix_file = LOCAL / "object-prefix"
    if not prefix_file.exists(): prefix_file.write_text("sweep-" + secrets.token_hex(6))
    prefix = prefix_file.read_text().strip()
    config_base = json.loads((ROOT / "rivet/engine/overrides.json").read_text())
    leader = infra["nodes"][0]["private_ip"]
    bundle = LOCAL / "app.tar.gz"
    subprocess.run(["tar", "-czf", str(bundle), "package.json", "pnpm-lock.yaml", "node_modules", "rivet/app", "celld/app", "driver"], cwd=ROOT, check=True)
    artifacts = LOCAL / "artifacts"
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (artifacts / "rivet-engine", artifacts / "celld", artifacts / "node.tar.xz")}
    assert hashes == json.loads((ROOT / "versions.sha256.json").read_text()), "Artifact checksum mismatch; run scripts/fetch.py"

    def install(host):
        name = host["name"]
        ssh(host, f"sudo mkdir -p {REMOTE}/bin /var/log/rivet-cost; sudo chown -R $(id -u):$(id -g) {REMOTE} /var/log/rivet-cost")
        upload(host, artifacts / "node.tar.xz", REMOTE + "/node.tar.xz")
        upload(host, bundle, REMOTE + "/app.tar.gz")
        ssh(host, f"set -e; cd {REMOTE}; mkdir -p node; tar -xJf node.tar.xz -C node --strip-components=1; tar -xzf app.tar.gz; chmod 755 node/bin/node")
        host_dir = LOCAL / "deploy" / name
        host_dir.mkdir(parents=True, exist_ok=True)
        if host in infra["nodes"]:
            for binary in ("rivet-engine", "celld"):
                upload(host, artifacts / binary, REMOTE + "/bin/" + binary)
            config = copy.deepcopy(config_base)
            config.update({
                "guard": {"host": "0.0.0.0", "port": 6420},
                "api_peer": {"host": "0.0.0.0", "port": 6421, "advertise_address": f"{host['private_ip']}:6421"},
                "metrics": {"host": "0.0.0.0", "port": 6430},
                "telemetry": {"enabled": False}, "auth": {"admin_token": token},
                "object_storage": {"primary": {"azure": {"account": storage["rivet"]["account"], "container": "benchmark", "prefix": prefix, "access_key": keys["rivet"]}}},
                "topology": {"datacenter_label": 1, "datacenters": {"default": {"datacenter_label": 1, "is_leader": True, "public_url": f"http://{leader}:6420", "peer_url": f"http://{leader}:6421"}}},
            })
            config["storage"]["log_directory"] = f"/var/lib/rivet/cost-{prefix}/rivet"
            secret_file(host_dir / "engine.json", json.dumps(config))
            config["api_peer"].update({"port": 6521, "advertise_address": f"{host['private_ip']}:6521"})
            config["metrics"]["port"] = 6531
            secret_file(host_dir / "operator.json", json.dumps(config))
            secret_file(host_dir / "engine.env", "RUST_LOG=info\nRUST_BACKTRACE=1\n")
            secret_file(host_dir / "runner.env", f"NODE_ENV=production\nRIVET_LOG_LEVEL=info\nRUST_LOG=info\nPORT=3100\nRIVET_NODE_VERSION=1\nRIVET_ENDPOINT=http://default:{token}@{host['private_ip']}:6420\nPATH={REMOTE}/node/bin:/usr/local/bin:/usr/bin:/bin\n")
            secret_file(host_dir / "celld.env", f"AZURE_STORAGE_ACCOUNT_NAME={storage['celld']['account']}\nAZURE_STORAGE_ACCOUNT_KEY={keys['celld']}\nCELLD_WATCH=/var/lib/rivet/cost-{prefix}/celld\nCELLD_NODE=cost-{name}\nPATH={REMOTE}/node/bin:{REMOTE}/node_modules/.bin:/usr/local/bin:/usr/bin:/bin\n")
            if args.scenario == 'churn':
                with (host_dir / 'celld.env').open('a') as f: f.write('CELLD_IDLE_EVICT_S=1\n')
            (host_dir / "cost-rivet-engine.service").write_text(unit("engine", f"{REMOTE}/bin/rivet-engine --config {REMOTE}/engine.json", "engine.env"))
            (host_dir / "cost-rivet-runner.service").write_text(unit("runner", f"{REMOTE}/node/bin/node --import tsx rivet/app/index.ts", "runner.env"))
            (host_dir / "cost-celld.service").write_text(unit("celld", f"{REMOTE}/bin/celld --bucket az://benchmark/{prefix} --listen 0.0.0.0:8080 --internal-listen {host['private_ip']}:8081 --advertise {host['private_ip']}:8081", "celld.env"))
            upload(host, str(host_dir) + "/", REMOTE + "/")
            ssh(host, f"sudo systemctl stop cost-rivet-runner cost-rivet-engine cost-celld 2>/dev/null || true; mkdir -p /var/lib/rivet/cost-{prefix}/rivet /var/lib/rivet/cost-{prefix}/celld; sudo cp {REMOTE}/*.service /etc/systemd/system/; sudo systemctl daemon-reload", timeout=180)
        else:
            secret_file(host_dir / "driver.env", f"RIVET_TOKEN={token}\nPATH={REMOTE}/node/bin:/usr/local/bin:/usr/bin:/bin\n")
            upload(host, host_dir / "driver.env", REMOTE + "/driver.env")
        print(f"{name}: installed pinned benchmark files", flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        hosts=infra['nodes']+infra['loadgens']
        if args.hosts:
            assert set(args.hosts)<=set(h['name'] for h in hosts)
            hosts=[h for h in hosts if h['name'] in args.hosts]
        list(pool.map(install, hosts))
    print("Both platform services installed; no timed workload has started.", flush=True)


if __name__ == "__main__": main()
