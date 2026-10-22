"""Check portable SSH helper quoting without connecting to any host."""
import json
import os
import shlex
from pathlib import Path
import subprocess
import tempfile
import unittest


class ScriptWrapperTests(unittest.TestCase):
    def test_remote_python_preserves_arguments_and_streams_helper(self):
        common = Path(__file__).resolve().parents[1] / 'openshift/community/lib/common.sh'
        with tempfile.TemporaryDirectory(prefix='okd wrapper ') as tmp:
            root = Path(tmp)
            # Emulate SSH's command argument locally, leaving stdin untouched.
            ssh = root / 'ssh'
            ssh.write_text('#!/usr/bin/env bash\nexec bash -c "${@: -1}"\n')
            ssh.chmod(0o700)
            helper = root / 'helper.py'
            helper.write_text('import json,sys; print(json.dumps(sys.argv[1:]))\n')
            values = [str(root / 'remote work'), 'literal$(false)', 'a; b', "quote'\"value"]
            script = 'source "$1"; STATE_DIR=$2; remote_python "$3" "${@:4}"'
            env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ['PATH'])
            result = subprocess.run(['bash', '-c', script, '--', str(common), str(root), str(helper), *values],
                                    capture_output=True, text=True, env=env, check=True)
            self.assertEqual(json.loads(result.stdout), values)

    def test_installer_password_is_redacted(self):
        result = subprocess.run(['sed', '-E', 's/(password: ).*/\\1[read protected auth file]/'],
                                input='level=info password: "example-secret"\nlevel=info Install complete!\n',
                                text=True, capture_output=True, check=True)
        self.assertNotIn('example-secret', result.stdout)
        self.assertIn('Install complete!', result.stdout)

    def test_admin_preparation_does_not_contact_planned_target(self):
        self.admin_preparation('198.51.100.20')

    def test_admin_preparation_without_node_ip_has_only_admin_host(self):
        self.admin_preparation('')

    def test_ssh_only_refresh_without_node_ip_does_not_connect_anywhere(self):
        self.admin_preparation('', ssh_only=True)

    def admin_preparation(self, node_ip, ssh_only=False):
        script = Path(__file__).resolve().parents[1] / 'openshift/community/01-prepare-host.sh'
        with tempfile.TemporaryDirectory(prefix='okd preparation ') as tmp:
            root = Path(tmp)
            key = root / 'private key'
            key.write_text('dummy test key')
            key.chmod(0o600)
            calls = root / 'ssh-calls.jsonl'
            ssh = root / 'ssh'
            ssh.write_text('#!/usr/bin/env python3\nimport os,sys,json\n'
                           'with open(os.environ["SSH_TEST_CALLS"],"a") as f: f.write(json.dumps(sys.argv[1:])+"\\n")\n'
                           'sys.stdin.read()\n')
            ssh.chmod(0o700)
            keygen = root / 'ssh-keygen'
            keygen.write_text('#!/usr/bin/env bash\nprintf "ssh-ed25519 TEST_PUBLIC_KEY\\n"\n')
            keygen.chmod(0o700)
            config = root / 'private.env'
            values = dict(ADMIN_HOST='192.0.2.10', ADMIN_USER='opc',
                          ADMIN_WORKDIR='/home/test/admin work', TARGET_USER='core',
                          NODE_IP=node_ip, SSH_IDENTITY=str(key))
            config.write_text(''.join(k+'='+shlex.quote(v)+'\n' for k,v in values.items()))
            env = dict(os.environ, PATH=str(root)+os.pathsep+os.environ['PATH'],
                       OKD_CONFIG_FILE=str(config), OKD_STATE_DIR=str(root/'private state'),
                       SSH_TEST_CALLS=str(calls))
            # An unrelated exported kubeconfig must not affect this fixture.
            env.pop('KUBECONFIG', None)
            subprocess.run(['bash', str(script)]+(['--ssh-only'] if ssh_only else []), env=env, check=True, capture_output=True, text=True)
            requests = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
            self.assertEqual(len(requests), 0 if ssh_only else 1)
            if requests:
                self.assertIn('okd-admin', requests[0])
                self.assertNotIn('okd-target', requests[0])
            state_config = (root/'private state'/'ssh_config').read_text()
            if not node_ip: self.assertNotIn('Host okd-target',state_config)
            self.assertIn('UserKnownHostsFile "'+str(root/'private state'/'known_hosts')+'"', state_config)


if __name__ == '__main__':
    unittest.main()
