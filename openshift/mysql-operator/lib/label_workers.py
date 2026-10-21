"""Label only explicit, Ready worker nodes; reject control-plane nodes."""
import json
import os
from pathlib import Path
import subprocess
import sys


def command(args):
    context = ['--context', os.environ['KUBE_CONTEXT']] if os.environ.get('KUBE_CONTEXT') else []
    result = subprocess.run(['oc', '--kubeconfig', os.environ['KUBECONFIG'], *context, '--request-timeout=30s', *args],
                            text=True, capture_output=True, timeout=45, check=True)
    return json.loads(result.stdout) if result.stdout.strip() else None


def select_workers(document, requested):
    if not requested or len(set(requested)) != len(requested):
        raise ValueError('Provide distinct MYSQL_WORKER_NAMES')
    selected = []
    for short in requested:
        matches = [n for n in document['items'] if n['metadata']['name'] == short
                   or n['metadata']['name'].split('.')[0] == short]
        if len(matches) != 1:
            raise ValueError('Missing or ambiguous worker: ' + short)
        node = matches[0]
        labels = node['metadata'].get('labels', {})
        if any(key in labels for key in ('node-role.kubernetes.io/control-plane', 'node-role.kubernetes.io/master')):
            raise ValueError('Refusing control-plane node: ' + short)
        if 'node-role.kubernetes.io/worker' not in labels:
            raise ValueError('Node is not a worker: ' + short)
        if node.get('spec', {}).get('unschedulable') or any(
                x['effect'] in ('NoSchedule', 'NoExecute') for x in node.get('spec', {}).get('taints', [])):
            raise ValueError('Worker is cordoned or has a scheduling taint: ' + short)
        if not any(x['type'] == 'Ready' and x['status'] == 'True' for x in node['status']['conditions']):
            raise ValueError('Worker is not Ready: ' + short)
        if labels.get('workload') not in (None, 'mysql-cluster'):
            raise ValueError('Worker has another workload label: ' + short)
        selected.append(node)
    return selected


def main():
    if not os.environ.get('KUBECONFIG') or not Path(os.environ['KUBECONFIG']).is_file():
        raise ValueError('Source the shared environment; configured KUBECONFIG must exist')
    current = command(['config', 'view', '--minify', '-o', 'json'])['clusters'][0]['cluster']['server']
    if current != os.environ['MYSQL_EXPECTED_API_SERVER']:
        raise ValueError('Unexpected cluster API; refusing node changes')
    selected = select_workers(command(['get', 'nodes', '-o', 'json']), os.environ['MYSQL_WORKER_NAMES'].split())
    operations = []
    for node in selected:
        patch = [{'op': 'test', 'path': '/metadata/uid', 'value': node['metadata']['uid']},
                 {'op': 'test', 'path': '/metadata/resourceVersion', 'value': node['metadata']['resourceVersion']},
                 {'op': 'add', 'path': '/metadata/labels/workload', 'value': 'mysql-cluster'},
                 {'op': 'add', 'path': '/metadata/labels/node-role.kubernetes.io~1mysql-cluster', 'value': ''}]
        args = ['patch', 'node', node['metadata']['name'], '--type=json', '-p', json.dumps(patch), '-o', 'json']
        command([*args, '--dry-run=server'])
        operations.append(args)
    # No worker is changed until every requested node passes identity/role/dry-run checks.
    state = Path(os.environ['MYSQL_STATE_DIR'])
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    journal = state / 'workers-before-label.json'
    if not journal.exists():
        journal.write_text(json.dumps(selected, indent=2)+'\n')
        journal.chmod(0o600)
    for args in operations:
        command(args)
    print('Labelled %d Ready worker nodes: workload=mysql-cluster; control plane excluded.' % len(selected))


if __name__ == '__main__':
    main()
