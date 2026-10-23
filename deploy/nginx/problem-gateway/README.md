# 문제 접속용 Nginx Gateway

`*.gate.msgctf.kr:443`에서 TLS를 종료하고 SNI(클라이언트가 보낸 서버 이름)별로
문제 VM의 IP와 포트에 연결하는 전용 Gateway 설정이다. Broker/Agent용
`../msg-broker.conf`와는 별도로 전용 VM에 설치한다.

현재 포함된 설정은 다음과 같다.

- `gate.msgctf.kr`은 로컬 HTTP 서버의 `/healthz`로 상태를 확인한다.
- 등록되지 않은 SNI와 SNI 없는 연결은 TLS handshake를 거절한다.
- 실제 문제 VM으로 연결하는 경로는 아직 등록하지 않았다.
- 인증서는 30일짜리 임시 self-signed 인증서다. 공개 서비스용 인증서가 아니다.
- Runtime의 경로 자동 등록·삭제 API는 아직 구현되지 않았다.

## 전용 VM 최초 설치

Nginx **1.25.5 이상**과 stream SSL 모듈이 필요하다. Ubuntu 26.10의 Nginx
1.30.4에서 설정 문법 검사와 서비스 reload를 확인했다. 참가자 외부 접속과
문제 VM 연결은 별도 검증이 필요하다.

아래 명령은 저장소 루트에서 실행한다. 기존 서비스가 있는 서버에 그대로
적용하지 않는다. 인증서 생성은 새 서버의 최초 설치 때만 실행한다.

```sh
sudo apt-get update
sudo apt-get install -y nginx libnginx-mod-stream openssl certbot
sudo install -d -m 700 /var/backups/msgctf-gateway
sudo cp -a /etc/nginx "/var/backups/msgctf-gateway/nginx-$(date -u +%Y%m%dT%H%M%SZ)"
sudo install -d -m 755 /etc/nginx/stream-conf.d
sudo install -d -m 700 /etc/nginx/tls/msgctf-gateway

sudo openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 \
  -noenc -sha256 -days 30 -subj '/CN=*.gate.msgctf.kr' \
  -addext 'subjectAltName=DNS:*.gate.msgctf.kr,DNS:gate.msgctf.kr' \
  -addext 'basicConstraints=critical,CA:FALSE' \
  -addext 'extendedKeyUsage=serverAuth' \
  -keyout /etc/nginx/tls/msgctf-gateway/privkey.pem \
  -out /etc/nginx/tls/msgctf-gateway/fullchain.pem
sudo chmod 600 /etc/nginx/tls/msgctf-gateway/privkey.pem

sudo install -m 644 deploy/nginx/problem-gateway/stream.conf /etc/nginx/stream-conf.d/00-msgctf-gateway.conf
sudo install -m 644 deploy/nginx/problem-gateway/health.conf /etc/nginx/conf.d/msgctf-gateway-health.conf
sudo install -d -m 755 /etc/letsencrypt/renewal-hooks/deploy
sudo install -m 755 deploy/nginx/reload-nginx-after-renewal.sh /etc/letsencrypt/renewal-hooks/deploy/20-msgctf-nginx-reload
```

`/etc/nginx/nginx.conf`의 `http { ... }` 바깥에 다음 블록을 한 번 추가한다.
`http` 안의 `server_tokens`는 `off`로 설정한다.

```nginx
stream {
    include /etc/nginx/stream-conf.d/*.conf;
}
```

새 서버의 기본 welcome site symlink인 `/etc/nginx/sites-enabled/default`를
제거한 뒤 다음 명령으로 설정을 반영한다.

```sh
sudo nginx -t && sudo systemctl reload nginx
sudo systemctl enable --now certbot.timer
```

## DNS와 공개 인증서

가비아 `msgctf.kr` DNS 관리에서 A 레코드 `gate`, `*.gate`를 Gateway 고정 공인
IP로 연결한다. Gateway에는 참가자용 TCP 443 도달이 가능해야 한다.
DNS 레코드만 추가해도 문제 경로가 자동 등록되지는 않는다.

현재 Certbot timer와 deploy hook은 준비 단계다. 갱신 대상 공인 인증서는
발급하지 않았다. 인증서 발급 후 `stream.conf`의 두 인증서 경로를 실제
인증서와 개인키 경로로 바꾸고 reload해야 한다. 개인키와 발급된 인증서는
VM에만 보관하고 Git에 추가하지 않는다.

일반 DNS-01 방식의 와일드카드 자동갱신에는 DNS 검증 자동화가 필요하다.
TXT 레코드를 수동 입력하는 것만으로는 자동갱신되지 않는다. Google의
DNS-PERSIST-01 방식은 별도 지원 클라이언트 연동이 필요하며 현재 구성에는
포함되지 않았다.

## 문제별 경로

예를 들어 아래 내용의 파일을 `/etc/nginx/stream-conf.d/`에 추가하고 문법
검사 후 reload하면 주소 하나를 문제 목적지 하나에 연결할 수 있다. 아래
주소와 IP는 예시이며 실제 배포 정보로 바꿔야 한다.

```nginx
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name example-instance.gate.msgctf.kr;
    proxy_pass 203.0.113.10:3934;
}
```

Gateway에서 TLS를 종료하므로 목적지는 평문 HTTP 또는 TCP 서비스다.
참가자는 웹 문제에 HTTPS로 접속하고, 일반 TCP 문제에는 TLS/SNI를 지원하는
클라이언트를 사용한다. 평문 `nc` 연결에는 주소별 분기가 적용되지 않는다.
다른 프로젝트 VM과도 실제 IP 라우팅과 방화벽 허용이 필요하며 Project ID만
등록해서 연결되는 구조가 아니다.

Runtime 자동화는 문제 생성 후 주소 등록, 등록 성공 후 참가자 주소 제공,
문제 종료 후 경로 삭제 순서로 구현해야 한다. 등록 권한은 Runtime으로
제한하고 목적지 IP·포트와 호스트명을 검증해야 한다. 기존 경로가 남은
상태에서 문제 포트를 재사용하면 다른 문제에 연결될 수 있다.

문제 VM의 포트를 인터넷 전체에 공개하면 Gateway를 우회할 수 있으므로,
문제 포트에는 Gateway에서 들어오는 연결만 허용하는 방화벽 구성이 필요하다.

## 사용자가 확인할 명령

DNS 연결 전에도 Windows PowerShell에서 다음 명령으로 확인할 수 있다.
`<GATEWAY_STATIC_IP>`를 실제 IP로 바꾸면 된다. `-k`는 임시 인증서 확인용이며
공인 인증서 적용 후에는 제거한다.

```powershell
curl.exe -k --resolve gate.msgctf.kr:443:<GATEWAY_STATIC_IP> https://gate.msgctf.kr/healthz
```

예상 응답은 `MSGCTF Gateway ready`다. 이후 실제 문제 VM 하나를 연결해서
정상 주소 접속, 미등록 주소 거절, 경로 삭제 후 접속 거절을 확인한다.

참고: [Nginx stream SNI](https://nginx.org/en/docs/stream/ngx_stream_core_module.html#server_name),
[Certbot 갱신](https://eff-certbot.readthedocs.io/en/stable/using.html#renewing-certificates),
[Google DNS-PERSIST-01](https://developers.google.com/public-key-infrastructure/updates/september2026-dns-persist-01).
