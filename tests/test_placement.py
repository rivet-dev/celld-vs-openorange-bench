"""Verify that the fixed tablet count remains balanced after node expansion."""
import sys,unittest,uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
from placement import validate

class PlacementTests(unittest.TestCase):
 def fixture(self,nodes):
  workers=[uuid.UUID(int=i+1) for i in range(nodes)]
  return {'tablets':[{'maintainer':str(workers[i%nodes]),'replicaSet':{'members':[list(workers[(i+j)%nodes].bytes) for j in range(3)]},'hashRange':{'start':hex(i*2**128//24),'end':hex((i+1)*2**128//24) if i<23 else None}} for i in range(24)]}
 def test_same_tablets_distributed_over_six_twelve_or_twenty_four_nodes(self):
  for nodes in [6,12,24]:
   owners=validate(self.fixture(nodes),node_count=nodes)
   self.assertEqual(len(owners),nodes)
   self.assertEqual(set(owners.values()),{24//nodes})
 def test_expanded_cluster_rejects_unbalanced_placement(self):
  data=self.fixture(12);data['tablets'][0]['maintainer']=data['tablets'][1]['maintainer']
  with self.assertRaises(AssertionError):validate(data,node_count=12)

if __name__=='__main__':unittest.main()
