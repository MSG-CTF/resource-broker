# MSG Broker Node Agent

각 k3s 노드에서 5분마다 다음 정보를 수집하고 중앙 Broker의
`POST /v1/agent/observations`로 전송한다.

- Node allocatable CPU, memory, ephemeral storage
- 실행 중인 Pod의 합산 resource requests
- 컨테이너별 CPU, memory, ephemeral-storage request
- 컨테이너 identity, 상태, 실제 CPU/memory/storage usage
- Node UID와 Ready 상태

Agent는 Kubernetes API만 사용한다. containerd socket이나 host PID/network를
마운트하지 않는다. 실제 usage는 API server를 통한 Kubelet Summary API에서
조회한다.

운영동형 설치는 `deploy/bootstrap/node-agent-bootstrap.sh`를 사용한다. 이
공통 Bootstrap이 Ubuntu AMD64/ARM64 VM에 지정 버전 k3s를 설치하고, VM-local CSR
enrollment, Node annotation, digest 고정 DaemonSet 적용과 실제 전송 확인까지
수행한다. 아래 수동 절차는 개발과 장애 진단용이다.

2026-08-10 깨끗한 GCP Ubuntu AMD64 VM에서 공통 Bootstrap을 실행해 k3s 신규
설치, VM-local CSR enrollment, digest 고정 DaemonSet rollout과 중앙 Broker
최초 observation 전달까지 검증했다.

## 이미지 빌드

저장소 루트에서 Docker Hub 사용자 이름과 버전을 정한 뒤 Buildx로 AMD64와
ARM64 이미지를 하나의 multi-platform image index로 배포한다.

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --tag <DOCKERHUB_USER>/msg-broker-node-agent:0.1.0 \
  --push \
  ./node-agent

docker buildx imagetools inspect \
  <DOCKERHUB_USER>/msg-broker-node-agent:0.1.0
```

운영 Bootstrap에는 tag가 아니라 registry가 반환한 `sha256` digest를 전달한다.
이때 개별 AMD64/ARM64 manifest digest가 아니라 두 플랫폼을 묶은 image index
digest를 사용해야 한다. containerd가 VM 아키텍처에 맞는 child image를 자동으로
선택한다.
`k8s/daemonset.yaml`의 tag는 수동 dry-run 기본값이며 Bootstrap이 적용 전에
digest로 렌더링한다.

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

## mTLS 운영 값

중앙 Broker HTTPS/mTLS Gateway와 VM별 enrollment 경로는 적용·검증됐다.
수동 manifest를 진단 목적으로 사용할 때는 다음 값을 설정한다. 운영동형 설치는
이 값을 직접 수정하지 않고 Bootstrap이 live manifest를 렌더링한다.

- `BROKER_OBSERVATIONS_URL`: `https://agents.mjsec.kr/v1/agent/observations`
- `AGENT_DRY_RUN`: `false`
- `/etc/msg-broker-agent/tls/client.crt`: 이 VM의 client certificate
- `/etc/msg-broker-agent/tls/client.key`: 이 VM의 client private key

Gateway 서버 인증서는 공개 Let's Encrypt 인증서이므로 Agent는 이미지의 시스템
신뢰 저장소로 이를 검증한다. `BROKER_CA_FILE`은 자체 서명 서버 인증서를 사용할
때만 설정하며, Agent client CA를 이 값으로 지정하면 안 된다.

Bootstrap이 TLS 디렉터리와 key 소유권을 UID/GID `10001`로 준비해야 한다.
private key는 해당 UID만 읽도록 제한한다. 인증서의 VM identity는 Node annotation의
`resource_target_id`와 일치해야 한다.

Broker 저장소의 `deploy/mtls/install-agent-certificate.sh`는 legacy 수동 발급
bundle을 `/etc/msg-broker-agent/tls`에 설치하는 진단/호환 경로다. 신규 설치는
VM-local private key가 밖으로 나오지 않는 Bootstrap enrollment를 사용한다.

## RBAC 범위

Agent ServiceAccount는 Node 조회, Pod 목록 조회, Kubelet Summary 조회를 위한
`nodes/proxy` GET만 가진다. workload 생성·변경·삭제 권한은 없다.
