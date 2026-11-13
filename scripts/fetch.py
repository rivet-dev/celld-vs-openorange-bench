#!/usr/bin/env python3
"""Fetch and verify the pinned Linux x86-64 release artifacts."""
import gzip,hashlib,json,subprocess,urllib.request
from common import ROOT,LOCAL

artifacts=LOCAL/'artifacts';artifacts.mkdir(parents=True,exist_ok=True)
pins=json.loads((ROOT/'versions.sha256.json').read_text())
urls={
 'node.tar.xz':'https://nodejs.org/dist/v24.10.0/node-v24.10.0-linux-x64.tar.xz',
 'celld':'https://github.com/denoland/celld/releases/download/v0.6.3/celld-x86_64-unknown-linux-gnu.gz',
}
for name,url in urls.items():
 path=artifacts/name
 if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest()!=pins[name]:
  with urllib.request.urlopen(url,timeout=120) as response: data=response.read()
  if name=='celld':data=gzip.decompress(data)
  assert hashlib.sha256(data).hexdigest()==pins[name],f'{name}: release checksum mismatch'
  path.write_bytes(data)
 if name=='celld':path.chmod(0o755)
 print(name,'verified')
subprocess.run(['bash',str(ROOT/'rivet/engine/fetch-engine.sh'),str(artifacts/'rivet-engine')],check=True)
subprocess.run(['pnpm','install','--frozen-lockfile','--ignore-scripts'],cwd=ROOT,check=True)
