# MSG CTF Resource Broker

여러 클라우드의 VM과 실행 노드를 모아 관리하고, 스케줄러에 사용 가능한 자원을 알려주는 서비스입니다
CPU·메모리·디스크 예약 원장을 기준으로 배정·확정·반납을 처리합니다

[플랫폼 전체 소개](https://github.com/MSG-CTF/msg-ctf.github.io)

## 맡은 일

- AWS·GCP·Azure 인벤토리를 공통 형식으로 수집
- 실행 조건에 맞는 후보 조회와 자원 예약·확정·반납
- 관리자 계정·노드·할당 용량 조회와 관리 콘솔
- 승인된 문제의 전용 VM 배정과 K3s 인증정보 제공
- 기동 CPU를 기본 CPU로 낮춰 확정하고 사용 가능한 용량에 반영
- 노드 초기 준비와 클라우드 인증 연결

## 함께 만든 사람들

| 기여자 | 기여 내용 | 관련 PR |
| --- | --- | --- |
| [gonas0919](https://github.com/gonas0919) | 멀티 클라우드 인벤토리, 후보·예약 API, 관리 콘솔과 노드 준비 | [#4](https://github.com/MSG-CTF/resource-broker/pull/4) |
| [jongcoding](https://github.com/jongcoding) | 전용 VM·할당량 연동, 인벤토리 오류 수정과 기동 CPU 예약 축소 | [#5](https://github.com/MSG-CTF/resource-broker/pull/5) · [#7](https://github.com/MSG-CTF/resource-broker/pull/7) · [#13](https://github.com/MSG-CTF/resource-broker/pull/13) |

2026-11-09까지 병합된 PR과 커밋을 기준으로 정리했습니다

## 기술 구성

Python · FastAPI · SQLAlchemy · PostgreSQL · React · Docker

## 개발·운영 문서

- [스케줄러 API](docs/SCHEDULER_API.md)
- [예약 검증](docs/RESERVATION_VALIDATION.md)
- [전용 VM](docs/DEDICATED_VM.md)
- [노드 준비 자동화](deploy/bootstrap/AUTOMATION.md)
- [기존 개발·설정 참고 문서](README.reference.md)
