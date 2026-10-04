# Windows 평가판 사용자 엔드포인트

Linux 호스트에서 Windows 커널을 공유하는 네이티브 컨테이너를 실행할 수는 없습니다. KT66은 `dockurr/windows` 실행 컨테이너 안에 **KVM/QEMU 가상머신**을 올립니다. 기본 자원은 2 vCPU, RAM 4 GiB, 가상 디스크 64 GiB이며 다른 서비스와 구분되는 `windows` 선택 프로필입니다.

설치 매체는 [Microsoft 공식 Windows 11 Enterprise 평가판](https://www.microsoft.com/en-us/evalcenter/download-windows-11-enterprise)의 한국어 Enterprise LTSC x64입니다. 평가 기간은 90일이며 만료 후 제한이 적용됩니다. 다운로드 도구는 Microsoft HTTPS 주소만 사용하고, 별도 미러나 제품 키·인증 우회는 사용하지 않습니다. [실행 컨테이너의 공식 안내](https://github.com/dockur/windows)도 참고하세요.

## 설치

호스트에서 `/dev/kvm`, `/dev/net/tun`, Docker Compose와 충분한 디스크 공간을 확인합니다. KT66의 FW·IPS가 실행 중이어야 합니다.

최초 KT66 설치에서 Windows 포함 여부는 **기본 No**입니다. 선택은 `.env`의 `WINDOWS_ENABLED`에 저장합니다. 나중에 추가하려면:

```bash
./kt66.sh windows
docker logs --tail 30 kt66-windows-user
```

`windows` 명령은 내부적으로 `download.py` → `setup.py` 순서로 실행합니다. 다운로드 단계는 ISO를 내려받고 크기와 로컬 SHA-256·출처를 `endpoints/windows/media/source.json`에 기록합니다. 설정 단계는 기록과 파일의 일치를 다시 확인합니다. 이미 설치한 VM의 디스크는 유지합니다. 관리 NIC 전용 Nginx 프록시도 함께 기동합니다. 로컬 해시는 다운로드 뒤 파일 변경을 탐지하며 Microsoft 게시 해시와 대조한 인증서라는 뜻은 아닙니다.

설치기는 `.env`에 임의의 `WINDOWS_PASSWORD`를 생성하고 실제 실행 이미지의 다이제스트를 `WINDOWS_IMAGE`로 고정합니다. 비밀번호·ISO·가상 디스크는 Git에 올리지 않습니다. 관리 콘솔은 기본 `http://<INT_HOST_IP>:8090/`, 계정은 `kt66student`입니다. 비밀번호는 강사가 호스트 `.env`에서 확인합니다. 포트 변경은 `.env`의 `PORT_WINDOWS_CONSOLE`과 자산 대장의 콘솔 주소를 함께 수정하세요.

자동 설치가 끝나면 `C:\OEM\install.log`에서 KT66 상태 서비스 설치 결과를 확인할 수 있습니다. `GET /health`는 실제 Windows OS 이름·버전·부팅 시각을 반환합니다. 명령 실행·파일 탐색 API는 제공하지 않습니다.

## 경로와 격리

```text
공격자 10.20.30.202 → FW → IPS 10.20.70.1 → user 10.20.70.0/24
                                                 └ Windows 실행 컨테이너 10.20.70.10
                                                    └ NAT 뒤의 Windows 게스트
```

`10.20.70.10`은 컨테이너 주소입니다. Windows 게스트는 그 안의 NAT 주소를 사용합니다. 사용자망은 Docker의 `internal` 브리지이며 기본 경로는 IPS→FW입니다. 내부 격리망에서는 Docker의 포트 publish가 적용되지 않으므로, 권한을 제거한 읽기 전용 Nginx 컨테이너가 호스트의 관리 IP에만 바인딩하여 콘솔을 중계합니다. 설치 콘솔의 응답(컨테이너 TCP/8006)은 호스트 관리 경로로 돌려보냅니다. 이 예외는 Windows 게스트의 일반 통신 경로가 아닙니다.

| 트래픽 | 정책 |
|---|---|
| 공격자 → Windows | ICMP, TCP/3389·8080 |
| 사용자망 → 인터넷 | DNS, NTP, HTTP, HTTPS |
| 사용자망 → 내부 DNS | `10.20.32.53` TCP/UDP 53 |
| 사용자망 → 메일 | `10.20.32.25` TCP 25·587·993 |
| 사용자망 → 웹 진입 | `10.20.32.80` TCP 80·443·8091 (WAF 경유) |
| 사용자망 → SIEM | TCP/1514·1515 등록·수집 경로만 허용 |
| 사용자망 → 그 외 내부·시설망 | 차단 |
| 설치 콘솔 → 관리 호스트 | 인증된 관리 화면의 응답 경로 |

기존 FW의 포트 DNAT는 FW 수신 주소에 한정합니다. 외부 웹사이트로 나가는 Windows HTTP/HTTPS가 기존 WAF로 잘못 전달되지 않도록 하는 설정입니다. 설치기는 기존 기본 룰만 원자적으로 바꾸며 사용자 추가 룰을 전체 초기화하지 않습니다. 새 Docker 브리지가 호스트 필터를 바꾸면 출처 IP가 변조될 수 있어 `netglue`를 다시 적용합니다.

```bash
# 강의용 공격자에서 실제 게스트 응답을 확인
docker exec kt66-attacker curl --max-time 10 http://10.20.70.10:8080/health
# 컨테이너 가동 여부와 게스트 응답은 따로 확인
docker inspect --format '{{.State.Status}}' kt66-windows-user
```

NOC 4층 운영 사무실 자산 목록의 **Windows**에서 콘솔과 경로·자원·주소 구분을 볼 수 있습니다. LED는 컨테이너 실행 상태이며 OS 설치 완료나 게스트 정상까지 보증하지 않습니다. SIEM 포트 허용과 Wazuh Windows 에이전트 설치는 별개입니다. 이 기본 설치는 Wazuh 에이전트를 자동 등록하지 않습니다.

## 중지·재기동

```bash
docker compose --profile windows stop windows-user windows-console
docker compose --profile windows up -d --no-deps windows-user windows-console
```

`windows-user-data` 볼륨이 OS 디스크입니다. 데이터 유지가 필요하면 볼륨을 삭제하지 마세요. FW/IPS를 새로 만들었으면 `setup.py`를 다시 실행해 사용자망 연결·정책을 확인합니다. 정책은 `endpoints/user-network.sh`, 게스트 상태 서비스는 `endpoints/windows/oem/`, 자산 등록은 `envsim/assets.yaml`에 있습니다.

메일 접속과 도메인 설정은 [내부 도메인·메일 매뉴얼](MAIL-DOMAIN.ko.md)을 참고하세요.
