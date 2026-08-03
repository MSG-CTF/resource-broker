# MSG Broker Node Agent

각 k3s 노드에서 5분마다 다음 정보를 수집하고 중앙 Broker의
`POST /v1/agent/observations`로 전송한다.

- Node allocatable CPU, memory, ephemeral storage
- 실행 중인 Pod의 resource requests
- 컨테이너 identity, 상태, 실제 CPU/memory/storage usage
- Node UID와 Ready 상태

Agent는 Kubernetes API만 사용한다. containerd socket이나 host PID/network를
마운트하지 않는다. 실제 usage는 API server를 통한 Kubelet Summary API에서
조회한다.

## 이미지 빌드

저장소 루트에서 Docker Hub 사용자 이름과 버전을 정한 뒤 실행한다.

```bash
docker build -t <DOCKERHUB_USER>/msg-broker-node-agent:0.1.0 ./node-agent
docker push <DOCKERHUB_USER>/msg-broker-node-agent:0.1.0
```

`k8s/daemonset.yaml`의 `image`를 같은 값으로 바꾼다.

## Canary k3s 노드 준비

Broker Admin의 VM 상세 화면에서 해당 VM의 `resource_target_id`를 확인하고,
k3s 노드에 다음 annotation을 설정한다.

```bash
sudo k3s kubectl get nodes
sudo k3s kubectl annotate node <NODE_NAME> \
  msg-broker.io/resource-target-id=<RESOURCE_TARGET_ID> --overwrite
```

한 VM에 single-node k3s가 하나라면 DaemonSet Pod도 하나만 실행된다. 여러 노드가
있는 클러스터에서는 각 Node에 서로 다른 Broker `resource_target_id` annotation이
필요하다.

## 수집만 먼저 확인

기본 manifest는 `AGENT_DRY_RUN=true`다. 따라서 HTTPS/mTLS가 준비되기 전에도
실제 k3s 수집 결과를 로그로 확인할 수 있고 Broker에는 전송하지 않는다.

```bash
sudo k3s kubectl apply -f node-agent/k8s/rbac.yaml
sudo k3s kubectl apply -f node-agent/k8s/daemonset.yaml
sudo k3s kubectl -n msg-broker-system rollout status daemonset/msg-broker-node-agent
sudo k3s kubectl -n msg-broker-system logs daemonset/msg-broker-node-agent --tail=100
```

최초 실행은 0~30초 사이에 무작위로 분산되고 이후 주기는 300초다. 수집이나 전송
한 번이 실패해도 Pod는 종료되지 않고 다음 주기에 다시 시도한다.

## mTLS 연결 시 변경할 값

다음 단계에서 Broker HTTPS/mTLS gateway와 VM별 인증서를 만든 뒤 manifest를
변경한다.

- `BROKER_OBSERVATIONS_URL`: 실제 HTTPS 주소
- `AGENT_DRY_RUN`: `false`
- `/etc/msg-broker-agent/tls/ca.crt`: Broker CA
- `/etc/msg-broker-agent/tls/client.crt`: 이 VM의 client certificate
- `/etc/msg-broker-agent/tls/client.key`: 이 VM의 client private key

Bootstrap이 TLS 디렉터리와 key 소유권을 UID/GID `10001`로 준비해야 한다.
private key는 해당 UID만 읽도록 제한한다. 인증서의 VM identity는 Node annotation의
`resource_target_id`와 일치해야 한다.

## RBAC 범위

Agent ServiceAccount는 Node 조회, Pod 목록 조회, Kubelet Summary 조회를 위한
`nodes/proxy` GET만 가진다. workload 생성·변경·삭제 권한은 없다.
