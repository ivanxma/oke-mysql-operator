"""Regional configuration and deployment isolation, with no remote calls."""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('region_checks', ROOT/'regions/lib/check_config.py')
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


class RegionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT/'internal_doc', prefix='region-tests-')
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.key = self.folder/'test key'
        self.key.write_text('synthetic test key')
        self.key.chmod(0o600)
        self.values = dict(OKD_REGION='uk-london-1', OCI_REGION='uk-london-1',
                           OCI_AUTH='instance_principal', ADMIN_HOST='192.0.2.10',
                           ADMIN_USER='testadmin', TARGET_USER='core',
                           ADMIN_WORKDIR='/home/testadmin/london', NODE_IP='198.51.100.20',
                           MACHINE_CIDR='198.51.100.0/24', POD_CIDR='10.132.0.0/14',
                           SERVICE_CIDR='172.31.0.0/16', CLUSTER_NAME='okd-london',
                           BASE_DOMAIN='lab.test', SSH_IDENTITY=str(self.key),
                           OCI_BUCKET='synthetic-media', OCI_NAMESPACE='synthetic-namespace',
                           OCI_COMPARTMENT_ID='ocid1.compartment.synthetic', OCI_COMPARTMENT_NAME='synthetic',
                           OKD_STATE_DIR=str(self.folder/'state'),
                           KUBECONFIG='/home/testadmin/london/sno/auth/kubeconfig')
        self.config = self.folder/'community.env'
        self.write_config()
        self.env = {k:v for k,v in os.environ.items() if not k.startswith(('OKD_', 'MYSQL_', 'MYSQLK8S_'))}
        self.env['OKD_CONFIG_FILE'] = str(self.config)

    def write_config(self):
        self.config.write_text(''.join(k+'='+shlex.quote(v)+'\n' for k,v in self.values.items()))
        self.config.chmod(0o600)

    def shell(self, code, success=True, **env):
        result = subprocess.run(['bash', '-c', code, '--', str(ROOT), str(self.config)],
                                env={**self.env, **env}, capture_output=True, text=True)
        self.assertEqual(result.returncode == 0, success, result.stdout+result.stderr)
        return result

    def test_shell_syntax_and_help_never_require_private_config(self):
        for path in (ROOT/'regions').rglob('*.sh'):
            subprocess.run(['bash', '-n', str(path)], check=True)
        for region in checks.REGIONS:
            for step in (ROOT/'regions'/region).glob('[0-9][0-9]-*.sh'):
                if step.name in ('00-init-config.sh', '00-check-config.sh'):
                    continue
                result = subprocess.run(['bash',str(step),'--help'], env={**self.env,'OKD_CONFIG_FILE':'/missing'},
                                        capture_output=True,text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('Usage:',result.stdout)

    def test_loader_replaces_inherited_kubeconfig_workspace_and_identity(self):
        code = 'import os,json; print(json.dumps({k:os.environ[k] for k in ["KUBECONFIG","ADMIN_WORKDIR","MYSQL_EXPECTED_API_SERVER","KUBE_CONTEXT"]}))'
        result = self.shell('source "$1/regions/uk-london-1/env.sh" ""; python3 -c ' + shlex.quote(code),
                            KUBECONFIG='/old/config', ADMIN_WORKDIR='/old/work', KUBE_CONTEXT='old',
                            MYSQL_EXPECTED_API_SERVER='https://old:6443')
        selected = json.loads(result.stdout)
        self.assertEqual(selected['KUBECONFIG'], self.values['KUBECONFIG'])
        self.assertEqual(selected['ADMIN_WORKDIR'], self.values['ADMIN_WORKDIR'])
        self.assertEqual(selected['KUBE_CONTEXT'], '')
        self.assertEqual(selected['MYSQL_EXPECTED_API_SERVER'], 'https://api.okd-london.lab.test:6443')

    def test_shared_installer_reloads_selected_profile(self):
        # The production peer inventory is private and unrelated to this fixture.
        # Test the loader wiring here; peer validation is covered directly below.
        import sys
        shim = self.folder/'python3'
        shim.write_text('#!/usr/bin/env bash\nif [[ ${1:-} == */regions/lib/check_config.py ]]; then exit 0; fi\nexec '+shlex.quote(sys.executable)+' "$@"\n')
        shim.chmod(0o700)
        self.env['PATH']=str(self.folder)+os.pathsep+self.env['PATH']
        self.shell('source "$1/regions/uk-london-1/env.sh" ""; source "$1/openshift/community/lib/common.sh"; '
                   'load_config; [[ $STATE_DIR == $OKD_STATE_DIR && $OCI_REGION == uk-london-1 ]]')

    def test_failed_profile_selection_preserves_previous_selection(self):
        self.values['OCI_REGION']='eu-frankfurt-1'; self.write_config()
        self.shell('export OKD_REGION=eu-frankfurt-1 KUBECONFIG=/old/config; '
                   'if source "$1/regions/uk-london-1/env.sh" "$2"; then exit 2; fi; '
                   '[[ $OKD_REGION == eu-frankfurt-1 && $KUBECONFIG == /old/config ]]')

    def test_wrong_region_rejected(self):
        self.values['OCI_REGION']='eu-frankfurt-1'; self.write_config()
        self.shell('source "$1/regions/uk-london-1/env.sh" ""', success=False)

    def test_non_instance_principal_rejected(self):
        self.values['OCI_AUTH']='api_key'; self.write_config()
        self.shell('source "$1/regions/uk-london-1/env.sh" ""', success=False)

    def test_unset_profile_fields_do_not_inherit_old_values(self):
        self.values.pop('NODE_IP'); self.values.pop('POD_CIDR'); self.values.pop('KUBECONFIG'); self.write_config()
        self.shell('source "$1/regions/uk-london-1/env.sh" ""; [[ -z $NODE_IP && -z $POD_CIDR && '
                   '$KUBECONFIG == /home/testadmin/london/sno/auth/kubeconfig ]]',
                   NODE_IP='192.0.2.99', POD_CIDR='10.0.0.0/8', KUBECONFIG='/old/config')

    def test_bucket_values_can_be_registered_after_host_preparation(self):
        cfg={**self.values,'OCI_BUCKET':'','OCI_NAMESPACE':''}
        errors,warnings=checks.validate(cfg)
        self.assertEqual(errors,[])
        self.assertTrue(any('02-create-bucket.sh' in w for w in warnings))

    def test_generated_bucket_environment_is_loaded_by_regional_profile(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location('registration',ROOT/'openshift/community/lib/prepare-bucket.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        self.values['OCI_BUCKET']='';self.values['OCI_NAMESPACE']='';self.write_config()
        state=Path(self.values['OKD_STATE_DIR']);state.mkdir()
        record=dict(region='uk-london-1',compartment_id=self.values['OCI_COMPARTMENT_ID'],
                    bucket='generated-bucket',namespace='generated-namespace')
        (state/'bucket.env').write_text(module.environment(record))
        self.shell('source "$1/regions/uk-london-1/env.sh" ""; '
                   '[[ $OCI_BUCKET == generated-bucket && $OCI_NAMESPACE == generated-namespace ]]')
        record['region']='eu-frankfurt-1';(state/'bucket.env').write_text(module.environment(record))
        self.shell('source "$1/regions/uk-london-1/env.sh" ""', success=False)

    def test_empty_node_ip_allowed_only_during_preparation(self):
        cfg={**self.values,'NODE_IP':''}
        errors,warnings=checks.validate(cfg,preparation=True)
        self.assertEqual(errors,[])
        self.assertFalse(any('NODE_IP' in w for w in warnings))
        errors,_=checks.validate(cfg)
        self.assertTrue(any('NODE_IP' in e for e in errors))

    def test_preparation_rejects_invalid_nonempty_node_ip(self):
        errors,_=checks.validate({**self.values,'NODE_IP':'invalid'},preparation=True)
        self.assertTrue(any('NODE_IP' in e for e in errors))

    def test_optional_file_is_loaded_and_primary_values_win(self):
        optional=self.config.with_name('community.optional.env')
        self.config=self.folder/'community.env';self.write_config()
        self.env['OKD_CONFIG_FILE']=str(self.config)
        optional.write_text('CLIENT_CIDR=192.0.2.0/24\nOCI_REGION=eu-frankfurt-1\n')
        self.shell('source "$1/regions/uk-london-1/env.sh" ""; [[ $CLIENT_CIDR == 192.0.2.0/24 && $OCI_REGION == uk-london-1 ]]')

    def test_no_node_ip_exported_until_generated_record_is_loaded(self):
        self.values.pop('NODE_IP');self.write_config()
        self.shell('source "$1/regions/uk-london-1/env.sh" ""; [[ ! ${NODE_IP+x} ]]', NODE_IP='192.0.2.99')
        state=Path(self.values['OKD_STATE_DIR']);state.mkdir()
        import importlib.util
        spec=importlib.util.spec_from_file_location('target_reg',ROOT/'openshift/community/lib/register-node-ip.py')
        registration=importlib.util.module_from_spec(spec);spec.loader.exec_module(registration)
        record=dict(region=self.values['OCI_REGION'],compartment_id=self.values['OCI_COMPARTMENT_ID'],
                    admin_workdir=self.values['ADMIN_WORKDIR'],cluster_name=self.values['CLUSTER_NAME'],
                    base_domain=self.values['BASE_DOMAIN'],machine_cidr=self.values['MACHINE_CIDR'],node_ip='198.51.100.22')
        (state/'node-ip.env').write_text(registration.environment(record))
        self.shell('source "$1/regions/uk-london-1/env.sh" ""; [[ $NODE_IP == 198.51.100.22 ]]')
        record['region']='eu-frankfurt-1';(state/'node-ip.env').write_text(registration.environment(record))
        self.shell('source "$1/regions/uk-london-1/env.sh" ""',success=False)

    def test_valid_offline_settings(self):
        errors, _ = checks.validate(self.values, config_path=self.config)
        self.assertEqual(errors, [])

    def test_missing_values_reported_together(self):
        cfg={**self.values, 'OCI_COMPARTMENT_ID':'', 'NODE_IP':'', 'BASE_DOMAIN':''}
        errors, _ = checks.validate(cfg)
        for key in ('OCI_COMPARTMENT_ID','NODE_IP','BASE_DOMAIN'):
            self.assertTrue(any(key in e for e in errors))

    def test_cross_site_cidr_collision_rejected(self):
        errors, _ = checks.validate(self.values, [{'POD_CIDR':self.values['POD_CIDR']}])
        self.assertTrue(any('overlaps the other region' in e for e in errors))

    def test_nested_remote_workspaces_rejected_on_same_admin(self):
        peer={'ADMIN_HOST':self.values['ADMIN_HOST'], 'ADMIN_WORKDIR':'/home/testadmin', 'OCI_REGION':'eu-frankfurt-1'}
        errors, _ = checks.validate(self.values, [peer])
        self.assertTrue(any('non-nested' in e for e in errors))

    def test_same_state_directory_rejected(self):
        errors, _ = checks.validate(self.values, [{'OKD_STATE_DIR':self.values['OKD_STATE_DIR']}])
        self.assertTrue(any('Local SSH/installer state' in e for e in errors))

    def test_same_cluster_identity_rejected(self):
        peer={k:self.values[k] for k in ('CLUSTER_NAME','BASE_DOMAIN')}
        errors, _ = checks.validate(self.values, [peer])
        self.assertTrue(any('cluster DNS identity' in e for e in errors))

    def test_different_compartment_rejected(self):
        errors, _ = checks.validate(self.values, [{'OCI_COMPARTMENT_ID':'ocid1.compartment.other'}])
        self.assertTrue(any('authorized compartment' in e for e in errors))

    def test_default_kubeconfig_rejected(self):
        errors, _ = checks.validate({**self.values, 'KUBECONFIG':str(Path.home()/'.kube/config')})
        self.assertTrue(any('KUBECONFIG' in e for e in errors))

    def test_config_mode_rejected(self):
        self.config.chmod(0o644)
        errors, _ = checks.validate(self.values, config_path=self.config)
        self.assertTrue(any('mode 600' in e for e in errors))

    def test_state_outside_private_directory_rejected(self):
        errors, _ = checks.validate({**self.values, 'OKD_STATE_DIR':'/tmp/state'})
        self.assertTrue(any('internal_doc' in e for e in errors))

    def test_stale_host_state_rejected_before_ssh(self):
        state=Path(self.values['OKD_STATE_DIR']); state.mkdir()
        (state/'host.env').write_text('SSH_HOST=192.0.2.99\nSSH_USER=testadmin\nTARGET_HOST=198.51.100.20\n')
        result=self.shell('source "$1/regions/uk-london-1/env.sh" ""; source "$1/openshift/community/lib/common.sh"; '
                          'load_host', success=False)
        self.assertIn('Saved SSH state', result.stderr)


if __name__ == '__main__':
    unittest.main()
