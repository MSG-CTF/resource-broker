"""Run as a separate service; observations come from a pinned, read-only SSH peer."""
import json
import os
from pathlib import Path
import subprocess
import time
from datetime import datetime
from uuid import UUID

from app.db.session import get_session_factory
from app.db.tables import ResourceTargetTable
from app.domain.enums import RuntimeType
from app.services.agent_observation_service import AgentObservationService, ObservationCapacity, ObservationUsage
from app.services.dedicated_vm import DedicatedVMPlacement


def observe(config):
    body=json.dumps({'action':'observe','target_id':config['target_id'],'profile_sha256':config['profile_sha256']})
    args=['ssh','-T','-p',str(config.get('ssh_port',22)),'-i',config['identity_file'],
      '-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','StrictHostKeyChecking=yes',
      '-o','UserKnownHostsFile='+config['known_hosts_file'],'-o','HostKeyAlias='+config['target_id'],
      '-o','ConnectTimeout=8',config['ssh_user']+'@'+config['ssh_host'],
      'sudo -n /usr/local/libexec/msgctf-vm-agent --observe-only']
    result=subprocess.run(args,input=body.encode(),capture_output=True,timeout=20)
    if result.returncode or len(result.stdout)>65536: raise RuntimeError('VM observer unavailable')
    data=json.loads(result.stdout)
    if not data.get('ok') or data.get('target_id')!=config['target_id']: raise RuntimeError('VM observation rejected')
    with get_session_factory()() as session:
        resource=session.get(ResourceTargetTable,UUID(config['resource_target_id']))
        if resource is None or resource.provider_instance_id!=data.get('provider_instance_id'):
            raise RuntimeError('VM identity does not match cloud inventory')
        if config['target_id'] not in DedicatedVMPlacement.from_environment().placements:
            raise RuntimeError('VM has no operator placement policy')
        return AgentObservationService(session).record(
          resource_target_id=resource.resource_target_id,observed_at=datetime.fromisoformat(data['observed_at']),
          snapshot_complete=True,runtime_type=RuntimeType.VM,runtime_target_id=config['target_id'],
          runtime_ready=data['ready'],node_allocatable=ObservationCapacity(**data['node_allocatable']),
          allocated_requests=ObservationCapacity(**data['allocated_requests']),
          node_usage=ObservationUsage(**data['node_usage']),containers=())


def main():
    path=os.environ['BROKER_DEDICATED_VM_OBSERVERS_FILE']
    while True:
        raw=Path(path).read_bytes()
        if len(raw)>65536: raise RuntimeError('VM observer configuration too large')
        config=json.loads(raw)
        for item in config['targets']:
            try:
                observe(item)
            except Exception:
                # Retain observation timestamps and capacity, but immediately withdraw readiness.
                with get_session_factory()() as session:
                    resource=session.get(ResourceTargetTable,UUID(item['resource_target_id']))
                    if resource is not None:
                        resource.ready=False
                        session.commit()
                print('VM_OBSERVATION_FAILED',flush=True)
        if os.getenv('BROKER_VM_OBSERVER_ONCE')=='1': return
        time.sleep(5)


if __name__=='__main__':main()
