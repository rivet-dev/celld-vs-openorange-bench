"""Incomplete sweeps require an explicit, complete prefix of measured holds."""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
from measurements import completed_levels

class MeasurementTests(unittest.TestCase):
 def metadata(self):return {'complete':False,'workload':{'levels':[8,16,32]},'stages':[{'actors':8},{'actors':16}]}
 def test_interrupted_run_is_not_implicitly_accepted(self):
  with self.assertRaises(AssertionError):completed_levels(self.metadata())
 def test_selection_preserves_the_interrupted_status(self):
  meta=self.metadata();meta['accepted_completed_levels']=[8,16]
  self.assertEqual(completed_levels(meta),[8,16]);self.assertFalse(meta['complete'])
 def test_selection_cannot_skip_or_invent_holds(self):
  for selected in [[16],[8,32],[8,16,32],[]]:
   meta=self.metadata();meta['accepted_completed_levels']=selected
   with self.assertRaises(AssertionError):completed_levels(meta)
 def test_complete_run_must_have_every_configured_hold(self):
  meta=self.metadata();meta['complete']=True
  with self.assertRaises(AssertionError):completed_levels(meta)

if __name__=='__main__':unittest.main()
