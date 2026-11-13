"""Inventory partitions must include every fixture and coordination object once."""
import importlib.util,json,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
from capacity import inventory_sizes

class Prefix:
 def __init__(self,name):self.name=name

class Client:
 def __init__(self,blobs):self.blobs=blobs
 def __enter__(self):return self
 def __exit__(self,*args):pass
 def list_blobs(self,*,name_starts_with,**kwargs):
  return (SimpleNamespace(name=name,size=size) for name,size in self.blobs.items() if name.startswith(name_starts_with))
 def walk_blobs(self,*,name_starts_with,delimiter,**kwargs):
  seen=set()
  for blob in self.list_blobs(name_starts_with=name_starts_with):
   tail=blob.name[len(name_starts_with):]
   if delimiter not in tail:yield blob;continue
   name=name_starts_with+tail.split(delimiter,1)[0]+delimiter
   if name not in seen:seen.add(name);yield Prefix(name)

class CapacityTests(unittest.TestCase):
 def test_parallel_inventory_covers_all_objects_without_overlap(self):
  blobs={'run/root.json':3,'run/cells/metadata.json':5,'run/cells/Other:opaque/data':7,'run/fleet/worker.json':11,'run/deploy/app.js':13,'elsewhere/data':9999}
  for i,digit in enumerate('0123456789abcdef'):
   blobs[f'run/cells/LoadActor:{digit}'+('0'*63)+'/own.json']=100+i
   blobs[f'run/cells/LoadActor:{digit}'+('0'*63)+'/ltx/e1/commit.ltx']=200+i
  with patch('azure.storage.blob.ContainerClient',side_effect=lambda *a,**k:Client(blobs)),patch('azure.storage.blob.BlobPrefix',Prefix):
   self.assertEqual(inventory_sizes({'account':'example','container':'benchmark'},'key','run','celld'),(sum(v for k,v in blobs.items() if k.startswith('run/')),len(blobs)-1))
 def test_other_versions_use_the_complete_unpartitioned_listing(self):
  blobs={'run/cells/LoadActor:non-hex/data':42,'run/control':3}
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);(root/'versions.json').write_text(json.dumps({'celld':'future'}))
   with patch('capacity.ROOT',root),patch('azure.storage.blob.ContainerClient',side_effect=lambda *a,**k:Client(blobs)):
    self.assertEqual(inventory_sizes({'account':'example','container':'benchmark'},'key','run','celld'),(45,2))

if __name__=='__main__':unittest.main()
