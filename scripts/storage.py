#!/usr/bin/env python3
"""Create isolated Azure Blob accounts for the two benchmark implementations."""
import json
import os
from pathlib import Path
import secrets
import subprocess

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local"


def az(*args, env=None):
    return subprocess.check_output(["az", *args, "-o", "json"], env=env, text=True)


def main():
    infra = json.loads((LOCAL / "infra.json").read_text())
    path = LOCAL / "storage.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    secret_path = LOCAL / "storage-keys.json"
    keys = json.loads(secret_path.read_text()) if secret_path.exists() else {}
    for platform in ("rivet", "celld"):
        if platform not in data:
            name = f"b{platform}{secrets.token_hex(4)}"
            account = json.loads(az("storage", "account", "create", "--name", name,
                "--resource-group", infra["resource_group"], "--location", infra["location"],
                "--sku", "Standard_LRS", "--kind", "StorageV2", "--min-tls-version", "TLS1_2",
                "--allow-blob-public-access", "false", "--tags", "purpose=rivet-celld-cost-benchmark"))
            data[platform] = {"account": name, "resource_id": account["id"], "container": "benchmark"}
            path.write_text(json.dumps(data, indent=2) + "\n")
        name = data[platform]["account"]
        if platform not in keys:
            response = json.loads(az("storage", "account", "keys", "list", "--account-name", name,
                                      "--resource-group", infra["resource_group"]))
            keys[platform] = response[0]["value"]
            fd = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(keys, f)
        env = dict(os.environ, AZURE_STORAGE_KEY=keys[platform])
        az("storage", "container", "create", "--account-name", name,
           "--name", data[platform]["container"], env=env)
        print(f"{platform}: dedicated Azure Blob account ready", flush=True)


if __name__ == "__main__":
    main()
