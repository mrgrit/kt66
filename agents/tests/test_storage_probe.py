"""디스크 실측의 경계값·미측정·대상 제한을 검사한다."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import storage_probe as disk

HEADER='Filesystem Type 1024-blocks Used Available Capacity Mounted on\n'
SAMPLE=HEADER+'/dev/root ext4 1000 740 185 80% /\n/dev/data xfs 1000 790 210 79% /data with space\ntmpfs tmpfs 100 100 0 100% /dev\n/dev/loop0 squashfs 100 100 0 100% /snap/test\n'

class Storage(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'agents';self.root.mkdir()
        (self.root.parent/'envsim').mkdir()
        (self.root.parent/'envsim/assets.yaml').write_text(json.dumps({'it_assets':[
            {'id':'app','name':'서비스','container':'kt66-app'},
            {'id':'remote','name':'원격 장비','remote':'10.0.0.2'},
            {'id':'runner','name':'러너','storage_source':'host'}]}))
        (self.root.parent/'docker-compose.yaml').write_text('services:\n  app: {container_name: kt66-app}\n  db: {container_name: kt66-db}\n')

    def test_threshold_uses_df_reserved_block_percentage_and_excludes_memory_images(self):
        rows,ignored=disk.parse_df(SAMPLE,80)
        self.assertEqual(len(rows),2);self.assertEqual(ignored,2)
        self.assertTrue(rows[0]['at_or_above_threshold'])
        self.assertFalse(rows[1]['at_or_above_threshold'])
        self.assertEqual(rows[0]['available_bytes'],185*1024)
        self.assertEqual(rows[1]['mountpoint'],'/data with space')

    def test_unknown_targets_and_arguments_never_execute(self):
        with patch.object(disk.subprocess,'run') as run:
            for target in ('$(touch /tmp/bad)','--privileged','missing','/etc'):
                with self.assertRaises(ValueError):disk.collect(self.root,target)
            for threshold in (0,101,True,80.5):
                with self.assertRaises(ValueError):disk.collect(self.root,threshold_pct=threshold)
            run.assert_not_called()

    def test_all_registered_targets_and_unconnected_systems_are_accounted_for(self):
        commands=[]
        def run(command,**kwargs):
            commands.append(command)
            self.assertNotIn('shell',kwargs)
            if 'kt66-db' in command:raise subprocess.TimeoutExpired(command,5)
            return subprocess.CompletedProcess(command,0,SAMPLE,'')
        with patch.object(disk.subprocess,'run',side_effect=run):result=disk.collect(self.root)
        self.assertEqual(result['target_count'],4)
        self.assertEqual(result['measured_targets'],2)
        self.assertEqual(result['matching_targets'],2)
        self.assertEqual({r['id'] for r in result['incomplete_targets']},{'remote','kt66-db'})
        self.assertEqual(result['status'],'partial')
        self.assertEqual(len(commands),3)
        self.assertIn(['docker','exec','-e','LC_ALL=C','kt66-app','df','-PkT'],commands)

    def test_host_alias_measures_host_once(self):
        with patch.object(disk.subprocess,'run',return_value=subprocess.CompletedProcess([],0,SAMPLE,'')) as run:
            result=disk.collect(self.root,'runner')
        self.assertEqual(result['target_count'],1)
        self.assertEqual(result['targets'][0]['id'],'host')
        self.assertEqual(run.call_args.args[0],['df','-PkT'])

    def test_error_and_malformed_output_never_mean_zero_percent(self):
        for rc,output in [(1,''),(0,'unexpected'),(0,HEADER+'invalid'),(1,SAMPLE)]:
            with self.subTest(rc=rc,output=output),patch.object(disk.subprocess,'run',return_value=subprocess.CompletedProcess([],rc,output,'failed')):
                result=disk.collect(self.root,'host')
                self.assertIn(result['status'],('partial','unavailable'))
                self.assertTrue(result['incomplete_targets'])
                if not output.endswith(SAMPLE):self.assertEqual(result['measured_targets'],0)

if __name__=='__main__':unittest.main()
