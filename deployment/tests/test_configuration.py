import contextlib
import io
import json
import os
import pty
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from configure import configure, valid_domain, dns_config, ROOT


class DomainTests(unittest.TestCase):
    def test_domain_rejects_config_and_shell_injection(self):
        for value in ['localhost','x;id.test','a.test\nserver=evil','a.test:80','https://a.test','-a.test','a..test','1.2.3.4','a.'+'b'*64]:
            with self.subTest(value=value),self.assertRaises(ValueError): valid_domain(value)
        self.assertEqual(valid_domain('School.Example.TEST.'),'school.example.test')

    def test_split_dns_and_mx(self):
        internal=dns_config('school.test','192.0.2.10','192.0.2.11',True)
        lan=dns_config('school.test','192.0.2.10','192.0.2.11')
        self.assertIn('host-record=mail.school.test,10.20.32.25',internal)
        self.assertIn('host-record=mail.school.test,192.0.2.11',lan)
        self.assertIn('host-record=noc.school.test,192.0.2.10',lan)
        self.assertIn('mx-host=school.test,mail.school.test,10',lan)
        self.assertIn('local=/school.test/',lan)

    def test_reconfiguration_preserves_accounts_and_updates_links(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{},clear=True):
            root=Path(tmp);(root/'.env').write_text('WEB_HOST_IP=192.0.2.10\nINT_HOST_IP=192.0.2.11\n')
            with contextlib.redirect_stdout(io.StringIO()): configure(root,'one.test')
            accounts=(root/'mail/accounts.json').read_bytes()
            initial=(root/'mail/credentials.local.json').read_bytes()
            with contextlib.redirect_stdout(io.StringIO()): configure(root,'two.test')
            self.assertEqual((root/'mail/accounts.json').read_bytes(),accounts)
            self.assertEqual((root/'mail/credentials.local.json').read_bytes(),initial)
            config=json.loads((root/'ui/deployment.json').read_text())
            self.assertEqual(config['mail']['webmail'],'https://webmail.two.test/')
            self.assertNotIn('one.test',(root/'deployment/runtime/hosts.txt').read_text())
            self.assertEqual((root/'mail/credentials.local.json').stat().st_mode & 0o777,0o600)

    def test_invalid_bind_address_fails_before_writing_domain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'.env').write_text('WEB_HOST_IP=0.0.0.0\n')
            with self.assertRaises(ValueError): configure(root,'school.test')
            self.assertNotIn('LAB_DOMAIN',(root/'.env').read_text())


class WindowsSelectionTests(unittest.TestCase):
    def selection(self, env_text='', override='', installed=False):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);(path/'.env').write_text(env_text)
            script=f'''source {ROOT}/kt66.sh
cd "$1"
docker() {{ return {0 if installed else 1}; }}
unset WINDOWS_ENABLED
{override}
resolve_windows_option
'''
            result=subprocess.run(['bash','-c',script,'test',tmp],text=True,capture_output=True,stdin=subprocess.DEVNULL)
            return result,(path/'.env').read_text()

    def test_interactive_enter_defaults_no(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);(path/'.env').write_text('')
            master,slave=pty.openpty()
            try:
                process=subprocess.Popen(['bash','-c',f'source {ROOT}/kt66.sh; cd "$1"; unset WINDOWS_ENABLED; docker() {{ return 1; }}; resolve_windows_option','test',tmp],stdin=slave,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                os.write(master,b'\n')
                stdout,stderr=process.communicate(timeout=5)
                self.assertEqual(process.returncode,0,stderr)
                self.assertIn('[y/N]',stdout)
                self.assertIn('WINDOWS_ENABLED=no',(path/'.env').read_text())
            finally:
                os.close(master);os.close(slave)

    def test_new_noninteractive_install_defaults_no(self):
        result,env=self.selection(); self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('WINDOWS_ENABLED=no',env)

    def test_existing_choice_and_installed_vm_preserved(self):
        for text,installed in [('WINDOWS_ENABLED=yes\n',False),('',True)]:
            result,env=self.selection(text,installed=installed)
            self.assertEqual(result.returncode,0,result.stderr);self.assertIn('WINDOWS_ENABLED=yes',env)

    def test_invalid_option_does_not_change_env(self):
        result,env=self.selection(override='WINDOWS_ENABLED=maybe')
        self.assertNotEqual(result.returncode,0);self.assertEqual(env,'')


if __name__=='__main__': unittest.main()
