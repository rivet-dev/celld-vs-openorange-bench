"""Independent checks of window boundaries, pricing and missing telemetry handling."""
import datetime,gzip,importlib.util,json,shutil,tempfile,unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('report',Path(__file__).parents[1]/'report/build.py')
report=importlib.util.module_from_spec(spec);spec.loader.exec_module(report)

class ReportTests(unittest.TestCase):
 def fixture(self,base):
  dest=base/'rivet';dest.mkdir();start=1800000000000
  (dest/'run.json').write_text(json.dumps({'complete':True,'stages':[{'actors':8,'start_ms':start,'end_ms':start+300000}]}))
  for shard in range(2):
   rows=[{'timestamp_ms':start+i*1000,'connected':4,'errors':0,'connect_errors':0} for i in range(300)]
   (dest/f'shard-{shard}-samples.ndjson').write_text(''.join(json.dumps(r)+'\n' for r in rows))
   events=[{'timestamp_ms':t,'type':'write','latency_ms':3} for t in [start-1,start,start+299999,start+300000]]
   (dest/f'shard-{shard}-events.ndjson').write_text(''.join(json.dumps(r)+'\n' for r in events))
  def metric(values):
   return {'metrics':[{'timeseries':[{'metadatavalues':[{'name':{'value':'apiname'},'value':api}],'data':[{'timeStamp':datetime.datetime.fromtimestamp((start+i*60000)/1000,datetime.timezone.utc).isoformat(),'total':v} for i,v in enumerate(counts)]} for api,counts in values.items()]}]}
  (dest/'azure-transactions.json').write_text(json.dumps(metric({'GetBlob':[300]*5,'PutBlob':[60]*5})))
  (dest/'azure-transactions-total.json').write_text(json.dumps(metric({'total':[360]*5})))
  (dest/'capacity-8.json').write_text(json.dumps({'started_ms':start+300001,'finished_ms':start+300100,'stored_bytes':1000000}))
  return dest
 def test_measured_window_and_cost(self):
  with tempfile.TemporaryDirectory() as folder:
   base=Path(folder);self.fixture(base);row=report.read_platform(base,'rivet')['rows'][0]
   self.assertEqual(row['writes'],4) # start included, end excluded, both shards
   self.assertEqual(row['requests'],{'GetBlob':1500,'PutBlob':300})
   self.assertAlmostEqual(row['cost_usd'],.0021)
   self.assertAlmostEqual(row['monthly_usd'],18.396)
   self.assertAlmostEqual(row['storage_monthly_usd'],.0000184)
 def test_missing_minute_is_not_zero(self):
  with tempfile.TemporaryDirectory() as folder:
   base=Path(folder);dest=self.fixture(base)
   p=dest/'azure-transactions-total.json';data=json.loads(p.read_text())
   data['metrics'][0]['timeseries'][0]['data'][2].pop('total');p.write_text(json.dumps(data))
   with self.assertRaisesRegex(AssertionError,'Missing aggregate'):report.read_platform(base,'rivet')
 def test_expanded_cluster_and_compressed_events(self):
  with tempfile.TemporaryDirectory() as folder:
   base=Path(folder);dest=self.fixture(base)
   p=dest/'run.json';meta=json.loads(p.read_text());meta.update(node_count=12,loadgen_count=2);p.write_text(json.dumps(meta))
   for p in dest.glob('shard-*-events.ndjson'):
    p.with_suffix('.ndjson.gz').write_bytes(gzip.compress(p.read_bytes(),mtime=0));p.unlink()
   row=report.read_platform(base,'rivet')['rows'][0]
   self.assertEqual(row['node_count'],12)
   self.assertEqual(row['writes'],4)
 def test_asynchronous_driver_samples_do_not_invent_disconnects(self):
  shards=[
   [{'timestamp_ms':0,'connected':4},{'timestamp_ms':1100,'connected':4},{'timestamp_ms':2200,'connected':8}],
   [{'timestamp_ms':500,'connected':4},{'timestamp_ms':1700,'connected':4},{'timestamp_ms':3400,'connected':8}],
  ]
  times,counts=report.combined_population(shards)
  self.assertEqual(times,[500,1100,1700,2200,3400])
  self.assertEqual(counts,[8,8,8,12,16])
 def test_unclassified_api_is_not_free(self):
  with self.assertRaisesRegex(AssertionError,'Unclassified'):report.cost({'FutureApi':100})
 def churn_fixture(self,base,lifetime=60000):
  dest=self.fixture(base)
  p=dest/'run.json';data=json.loads(p.read_text());data['workload']={'scenario':'churn'};p.write_text(json.dumps(data))
  for shard in range(2):
   p=dest/f'shard-{shard}-samples.ndjson'
   samples=[json.loads(line) for line in p.read_text().splitlines()]
   for sample in samples:sample.update(ready_slots=4,opening=0)
   p.write_text(''.join(json.dumps(row)+'\n' for row in samples))
   with (dest/f'shard-{shard}-events.ndjson').open('a') as f:
    for i in range(20):f.write(json.dumps({'type':'retired','actor':f'{shard}-{i}','timestamp_ms':1800000000000+i*15000,'lifetime_ms':lifetime,'close_latency_ms':2})+'\n')
  return dest
 def test_churn_requires_real_completed_lifetimes(self):
  with tempfile.TemporaryDirectory() as folder:
   base=Path(folder);dest=self.churn_fixture(base)
   row=report.read_platform(base,'rivet')['rows'][0]
   self.assertEqual(row['completed_lifetimes'],40)
   self.assertEqual(row['mean_connected'],8)
   for p in dest.glob('shard-*-events.ndjson'):
    p.write_text(''.join(line+'\n' for line in p.read_text().splitlines() if json.loads(line)['type']!='retired'))
   with self.assertRaisesRegex(AssertionError,'Missing complete'):report.read_platform(base,'rivet')
 def test_churn_rejects_thirty_second_lifetimes(self):
  with tempfile.TemporaryDirectory() as folder:
   base=Path(folder);self.churn_fixture(base,lifetime=30000)
   with self.assertRaisesRegex(AssertionError,'lifetime differs'):report.read_platform(base,'rivet')
 def test_setup_retries_are_counted_in_the_exact_window(self):
  with tempfile.TemporaryDirectory() as folder:
   base=Path(folder);dest=self.churn_fixture(base)
   with (dest/'shard-0-events.ndjson').open('a') as f:
    for stamp in [1799999999999,1800000000000,1800000299999,1800000300000]:
     f.write(json.dumps({'type':'connect_retry','timestamp_ms':stamp})+'\n')
   row=report.read_platform(base,'rivet')['rows'][0]
   self.assertEqual(row['connection_retries'],2)
   self.assertEqual(row['completed_lifetimes'],40)
 def test_transition_inventory_is_not_assigned_to_held_population(self):
  with tempfile.TemporaryDirectory() as folder:
   base=Path(folder);dest=self.fixture(base)
   with (dest/'shard-0-events.ndjson').open('a') as f:
    f.write(json.dumps({'type':'stage','timestamp_ms':1800000300050,'level':16})+'\n')
   row=report.read_platform(base,'rivet')['rows'][0]
   self.assertIsNone(row['stored_bytes'])
   self.assertIsNone(row['storage_monthly_usd'])
   self.assertEqual(row['storage_sample_status'],'inventory_crossed_population_change')
   self.assertAlmostEqual(row['monthly_usd'],18.396)
 def test_operation_totals_must_reconcile(self):
  with tempfile.TemporaryDirectory() as folder:
   base=Path(folder);dest=self.fixture(base)
   p=dest/'azure-transactions.json';data=json.loads(p.read_text());data['metrics'][0]['timeseries'][0]['data'][0]['total']=299;p.write_text(json.dumps(data))
   with self.assertRaisesRegex(AssertionError,'do not reconcile'):report.read_platform(base,'rivet')

if __name__=='__main__':unittest.main()
