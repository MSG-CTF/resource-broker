<div align="center">
<h1>MSG CTF · Resource Broker</h1>
<p>AWS·GCP·Azure의 실행 자원을 모아 배정·예약·반납하는 서비스</p>
<p><a href="https://msg-ctf.github.io/platform.html">플랫폼</a> · <a href="#development">개발 문서</a></p>
</div>

<h2 align="center">Overview</h2>

<table align="center">
<tr><th width="260">자원 수집</th><th width="260">배정·예약</th><th width="260">반납</th></tr>
<tr><td align="center">클라우드 인벤토리·노드 준비</td><td align="center">실행 후보·CPU·메모리·디스크</td><td align="center">예약 해제·기동 CPU 축소</td></tr>
</table>

<h2 align="center">Contributors</h2>

<table align="center">
<tr><th align="center" colspan="6"><a href="https://github.com/MSG-CTF/resource-broker">Resource Broker</a></th><th align="center" colspan="6"><a href="https://github.com/MSG-CTF">PM</a></th></tr>
<tr>
<td align="center" valign="top" colspan="6" width="390"><a href="https://github.com/gonas0919"><img src="https://avatars.githubusercontent.com/u/143320324?v=4&amp;s=128" width="52" height="52" alt="gonas0919"></a><br><a href="https://github.com/gonas0919"><b>gonas0919</b></a><br><sub>클라우드 인벤토리·예약<br>관리 콘솔·노드 준비</sub></td>
<td align="center" valign="top" colspan="6" width="390"><a href="https://github.com/jongcoding"><img src="https://avatars.githubusercontent.com/u/128033808?v=4&amp;s=128" width="52" height="52" alt="jongcoding"></a><br><a href="https://github.com/jongcoding"><b>jongcoding</b></a><br><sub>전용 VM·할당량 연동<br>인벤토리 수정·CPU 예약 축소</sub></td>
</tr>
</table>

<details>
<summary>기여 기록 · 관련 PR</summary>

| 기여자 | 기여 내용 | 관련 PR |
| --- | --- | --- |
| [gonas0919](https://github.com/gonas0919) | 멀티 클라우드 인벤토리, 후보·예약 API, 관리 콘솔과 노드 준비 | [#4](https://github.com/MSG-CTF/resource-broker/pull/4) |
| [jongcoding](https://github.com/jongcoding) | 전용 VM·할당량 연동, 인벤토리 오류 수정과 기동 CPU 예약 축소 | [#5](https://github.com/MSG-CTF/resource-broker/pull/5) · [#7](https://github.com/MSG-CTF/resource-broker/pull/7) · [#13](https://github.com/MSG-CTF/resource-broker/pull/13) |

</details>

<h2 align="center">Tech Stack</h2>

<p align="center"><img src="https://raw.githubusercontent.com/MSG-CTF/msg-ctf.github.io/67e93a09ce630910f98ae56fb118ca1317af78e0/assets/tech/fastapi.svg" width="44" height="44" alt="FastAPI" title="FastAPI"> &nbsp; <img src="https://raw.githubusercontent.com/MSG-CTF/msg-ctf.github.io/67e93a09ce630910f98ae56fb118ca1317af78e0/assets/tech/postgresql.svg" width="44" height="44" alt="PostgreSQL" title="PostgreSQL"> &nbsp; <img src="https://raw.githubusercontent.com/MSG-CTF/msg-ctf.github.io/67e93a09ce630910f98ae56fb118ca1317af78e0/assets/tech/react.svg" width="44" height="44" alt="React" title="React"> &nbsp; <img src="https://raw.githubusercontent.com/MSG-CTF/msg-ctf.github.io/67e93a09ce630910f98ae56fb118ca1317af78e0/assets/tech/docker.svg" width="44" height="44" alt="Docker" title="Docker"><br><sub>FastAPI · PostgreSQL · React · Docker</sub><br><sub>Python · SQLAlchemy · AWS · GCP · Azure</sub></p>

## Development

- [스케줄러 API](docs/SCHEDULER_API.md)
- [예약 검증](docs/RESERVATION_VALIDATION.md)
- [전용 VM](docs/DEDICATED_VM.md)
- [노드 준비 자동화](deploy/bootstrap/AUTOMATION.md)
- [설치·실행 안내](README.reference.md)

<p align="center"><sub><a href="https://github.com/MSG-CTF/msg-ctf.github.io/blob/67e93a09ce630910f98ae56fb118ca1317af78e0/docs/tech-icons.md">아이콘 출처</a></sub></p>
