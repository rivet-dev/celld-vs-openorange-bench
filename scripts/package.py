#!/usr/bin/env python3
"""Create a shareable directory and ZIP from an explicit public file allowlist."""
import shutil,zipfile
from common import ROOT

DEST=ROOT/'dist'/'bench-rivet-vs-celld'
DEST.mkdir(parents=True,exist_ok=True)
files=['README.md','LICENSE','package.json','pnpm-lock.yaml','requirements.txt','versions.json','versions.sha256.json','workload.json','workload-churn.json','workload-churn-6nodes.json','workload-churn-12nodes.json','workload-churn-24nodes.json','pricing.json','hardware.json','.gitignore']
allowed_roots=['rivet','celld','driver','scripts','infra','tests','results','report']
for folder in allowed_roots:
 for p in (ROOT/folder).rglob('*'):
  if not p.is_file() or p.is_symlink():continue
  rel=p.relative_to(ROOT)
  if any(part in {'node_modules','__pycache__','.terraform','.local','.git','.jj'} for part in rel.parts):continue
  if p.suffix in {'.pyc','.log','.tfstate','.tfvars'} or '.tfstate.' in p.name:continue
  files.append(str(rel))
# Refuse to leave stale material in an existing export: remove only files beneath
# this script-owned export folder that are absent from the new allowlist.
for p in DEST.rglob('*'):
 if p.is_file() and str(p.relative_to(DEST)) not in files:p.unlink()
for name in files:
 source=ROOT/name
 if not source.exists():continue
 dest=DEST/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,dest)
archive=ROOT/'dist'/'bench-rivet-vs-celld.zip'
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
 for p in sorted(DEST.rglob('*')):
  if p.is_file():z.write(p,str(p.relative_to(DEST.parent)))
print(DEST);print(archive)
