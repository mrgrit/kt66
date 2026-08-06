# kt66 — AI데이터센터 운영 교육 인프라

학생 1인당 서버 1대에 **미니 AI데이터센터**를 통째로 세운다. 망분리된 인프라, 가상 시설
계통(전력·냉방·소방·물리보안), GPU 추론 서비스, 그리고 각 층에서 일하는 **근무자 에이전트**까지
한 스택에 들어간다. 15주 교육과정(구축 → 운영 → 대응·자동화)의 실습 무대다.

> **베이스는 [el34](https://github.com/mrgrit/el34)다.** kt88 이 아니다.
> el34 에는 관리 포털(`portal/`), 에이전트 하네스(`bastion/`), 자동 채점·시나리오 주입
> (`assessor/`)이 이미 있고 **단일 호스트** 구성이라 1인 1대 배정과 맞는다. kt88 은
> 3대 macvlan 확장판이라 이 용도에는 불필요한 복잡도가 된다.
> 다만 GPU 서버를 엔드포인트로 붙이는 방식은 kt88 의 접근을 이어받는다(아래 GPU 존).

## el34 에서 무엇을 뺐고 무엇을 더했나

**뺀 것** — 메모리를 크게 먹고 이 과정에 필요 없는 것들.

| 제거 | 사유 |
|---|---|
| `docker-compose.misp.yml` | TI 플랫폼. 보안 심화용이라 DC 운영 과정에 불필요 |
| `docker-compose.opencti.yml` | 〃 (단독으로 수 GB) |
| `docker-compose.sysmon.yml` + `sysmon/` | Windows 엔드포인트 계측용 |
| Windows 엔드포인트 | KVM + 수 GB 이미지. el34 에서도 보류 상태였다 |
| `docker-compose.ollama.yml` | 추론은 GPU 서버(DGX Spark)에서 돈다. 로컬 중복 불필요 |
| `docker-compose.override.yaml` | 특정 호스트 전용 오버라이드. 9200 포트를 열어 충돌을 낸다 |

**더한 것**

| 추가 | 내용 |
|---|---|
| `agents/` | 런타임 중립 근무자 에이전트 스펙 + `agentctl` 렌더러 |
| GPU 존 | DGX Spark 를 WireGuard 로 랩 세그먼트에 편입 |
| 4층 물리 모델 | 시설 계통 + 층별 자산 배치 (시각화 UI 의 데이터 모델) |

## 층과 존 — 직교한다

**층(floor)은 물리 배치, 존(zone)은 네트워크 보안등급이다.** 같은 층 랙에 다른 존이 섞일 수
있고, 같은 존이 여러 층에 걸칠 수 있다. 이 어긋남 자체가 교보재다 — 물리적으로 옆자리인데
논리적으로 다른 망이라는 것을, 시각화 UI 에서 두 뷰를 겹쳐 보며 체감한다.

```
┌─ 4F  운영층 (NOC/SOC)  ─────────────────────── zone: mgmt ───┐
│  ITSM · CMDB · portal · Wazuh 대시보드 · 런북                 │
│  근무자: 서비스데스크 · SOC분석가 · 운영리드 · 감사인            │
│  ▶ W4 자산/텔레메트리 · W6 ITSM · W8 중간평가                  │
│    W14 컴플라이언스 · W15 에이전트 자동화                      │
└──────────────────────────────────────────────────────────────┘
┌─ 3F  AI 전산실 (GPU Zone)  ────────────────── zone: app ─────┐
│  DGX Spark · 오픈모델 서빙(Ollama) · 추론 SLA · GPU 쿼터       │
│  근무자: GPU/플랫폼 엔지니어                                   │
│  ▶ W3 AI 워크로드 편입 · W7 쿼터·용량 · W12 GPU 장애           │
│  ※ 랙당 전력밀도가 다른 층의 5~10배 — 1F 와 직접 연동          │
└──────────────────────────────────────────────────────────────┘
┌─ 2F  일반 전산실 (Core/Service)  ──── zone: dmz / int / pipe ┐
│  fw · ips · web(WAF) · 취약 웹앱 6종 · SIEM · 백업             │
│  근무자: 네트워크 엔지니어 · 시스템/스토리지 엔지니어            │
│  ▶ W2 인프라 배포 · W9 변경관리 · W10 백업 · W11 보안운영       │
└──────────────────────────────────────────────────────────────┘
┌─ 1F  시설·보안층 (전부 가상)  ─────────────── zone: ot ──────┐
│  수전·UPS·PDU │ 냉동기·CRAC·핫/콜드아일 │ 소방 │ 출입·CCTV    │
│  근무자: 시설 담당 · 물리보안                                  │
│  ▶ W1 전력밀도·냉방 · W5 환경 모델링 · W13 환경 장애           │
└──────────────────────────────────────────────────────────────┘

        의존 방향: 1F ──▶ 2F/3F ──▶ 4F   (단방향)
```

이 배치의 값어치는 **의존이 단방향**이라는 데 있다. 아래층 장애는 위로 번지고 위층 장애는
국소적이다. 그래서 13주차 *"환경 이상 → 시스템 장애 연쇄"* 가 별도 장치 없이 구조에서 성립한다 —
1F 냉동기를 죽이면 3F GPU 가 스로틀링되고, 추론 SLA 가 깨지고, 4F 에 티켓이 생긴다.

## 네트워크

el34 의 4-tier 를 그대로 계승한다. 패킷 흐름은 불변이다: **attacker → fw → ips → web → 앱**

| 존 | 대역 | 층 | 용도 |
|---|---|---|---|
| ext | 10.20.30.0/24 | — | 외부 — 공격자·bastion |
| pipe | 10.20.31.0/24 | — | fw ↔ ips 내부 회선 |
| dmz | 10.20.32.0/24 | 2F | web(WAF) · SIEM · portal |
| int | 10.20.40.0/24 | 2F | 취약 웹앱 (외부 노출 없음) |
| **app** | **10.20.50.0/24** | **3F** | **GPU 존 — DGX Spark (WireGuard)** |

취약 웹앱은 `web` 의 Apache vhost 리버스 프록시로만 도달한다. 외부에서 직접 갈 수 없다.

### GPU 존 — 왜 WireGuard 인가

DGX Spark 는 공인 IP(별도 회선)에 있고 랩 호스트는 NAT 뒤에 있다. 그래서

- **macvlan 불가** — 같은 L2 가 아니다
- **단순 L3 라우팅 불가** — 랩 호스트가 인바운드를 못 받는다 (Wazuh 에이전트는 매니저로
  *나가는* 방향인데, 그 목적지가 NAT 안이다)

WireGuard 는 NAT 를 통과하며 양방향 L3 를 만든다. DGX Spark 가 `10.20.50.10` 을 정식으로
갖고 그 트래픽이 fw/ips 를 지나므로, **"모든 트래픽은 보안장비를 지난다"는 성질이 유지된다.**

DGX Spark 는 aarch64(GB10)다. Wazuh 에이전트는 arm64 패키지가 4.10.x 부터 제공되므로
매니저(amd64 4.10.0)와 버전이 맞는다.

## 근무자 에이전트 — 런타임을 고른다

bastion 하나에 묶지 않는다. 페르소나는 중립 스펙으로 한 번 쓰고, 어댑터가 각 런타임으로
렌더한다. `agents/roster.yaml` 에서 **페르소나마다** 런타임을 지정한다.

```bash
cd agents
./agentctl list                        # 명단 + 층·존·런타임·자율성
./agentctl runtime soc-analyst hermes  # 런타임 교체
./agentctl render --all                # 각 런타임 형식으로 렌더
./agentctl diff facility-engineer      # 두 런타임 결과 비교 (W15 실습)
```

| | bastion | Hermes Agent | Claude Code |
|---|---|---|---|
| 루프/스케줄 | 없음 | **`cron/`** | — |
| 메모리 | KG(sqlite) | `memories/` + `state.db` | — |
| 샌드박스 | docker exec | `sandboxes/` | — |
| 다중 페르소나 하네스 | **있음** | 단일 | subagent |
| 외부 연동 | 자체 API | **`hermes acp`** | — |

12주(다역할 인시던트 대응)는 bastion 하네스가, 15주(루프 엔지니어링)는 Hermes 의 cron·메모리가
유리하다. 그래서 둘 다 둔다. 자세히는 [`agents/README.md`](agents/README.md).

## 배포

```bash
git clone https://github.com/mrgrit/kt66 && cd kt66
sudo ./kt66.sh install     # docker + daemon.json (최초 1회)
sudo ./kt66.sh up          # 16 컨테이너
```

수동으로 할 경우(호스트 systemd 를 건드리지 않는다):

```bash
cp .env.example .env && echo "WEB_HOST_IP=<이 서버 IP>" >> .env
ssh-keygen -t ed25519 -f keys/id_rsa -N ""
sudo ./kt66-hostip.sh                                   # 내부 GUI 용 dummy IF
sudo docker compose -f docker-compose.yaml up -d --build
sudo ./kt66-net.sh                                      # 인터-브리지 체인 글루
```

> `docker compose` 에 `-f docker-compose.yaml` 을 **명시**한다. 생략하면 compose 가 같은
> 디렉터리의 override 파일을 자동 병합한다.

## 접속

| 위치 | 주소 | 내용 |
|---|---|---|
| 웹 진입 | `http://<서버IP>/` | 랜딩 |
| 〃 | `:8001`~`:8007` | 취약 웹앱 6종 + AICompanion |
| 관리 포털 | `http://192.168.136.145:8000/` | 대시보드 |
| SIEM | **`https://`**`192.168.136.145:5601/` | Wazuh (HTTPS 다) |
| 콘솔 GUI | `http://192.168.136.145:8081~8083/` | nft / suricata / modsec |
| bastion SSH | `ssh ccc@<서버IP> -p 2204` | 점프 호스트 |

내부 GUI 는 dummy 인터페이스(`kt66int0`, 192.168.136.145)에만 열린다 — LAN 격리다.

## 검증된 동작

x86_64 / Ubuntu 22.04 / i9-12900K·31GB 에서 실측.

| 항목 | 결과 |
|---|---|
| 컨테이너 | **16개 전부 기동** |
| 체인 | attacker → `10.20.30.1`(fw) → `10.20.31.2`(ips) → `10.20.32.80`(web) |
| 웹 진입 | 랜딩 200 / juice 200 / DVWA 302 / 자체 취약앱 4종 200 |
| 내부 GUI | portal 200 · nft 200 · suricata 200 · modsec 200 |
| SIEM | wazuh-control 데몬 10개 running |
| 인덱서 | `_cluster/health` = **green** |
| 대시보드 | `https://…:5601` → 302 (로그인) |
| 빌드 시간 | 최초 8분 26초 (캐시 적중 시 20초) |

**GPU 존** — DGX Spark(`spark-1397`, GB10/aarch64, 119GB 통합메모리) 실측.

| 항목 | 결과 |
|---|---|
| 터널 | WireGuard 핸드셰이크 성립, 양방향 전송 |
| GPU 존 체인 | attacker → `10.20.30.1`(fw) → `10.20.31.2`(ips) → `10.20.50.2`(gpu-gw) → `10.20.50.10`(DGX) |
| RTT | 랩 ↔ DGX **약 3ms** |
| 역방향 | DGX → 랩 web `HTTP 200`, siem·ips 도달 |
| 추론 | 랩에서 `10.20.50.10:11434/v1/chat/completions` → **HTTP 200** (22.3초) |
| 모델 | 11종 조회 (`solar:100b` 62GB · `EXAONE-4.5-33B` · `qwen3.6:35b` 등) |
| 자산 편입 | Wazuh 에이전트 `dgx-spark-01` (ID 004) **Active**, v4.10.4 arm64 |
| DGX 기본 경로 | **보존** — 랩 대역만 터널, 인터넷은 자기 회선 |

**환경 시뮬레이터** — 시설은 가상이지만 **사용률은 실측**이다(컨테이너 CPU + GPU 상태).

| 항목 | 결과 |
|---|---|
| ENV-01 CRAC 정지 | 해당 아일 냉방 0kW, 온도 상승 개시, `L10 항온항습기 정지` |
| ENV-03 수전 상실 + 발전기 실패 | `L10 UPS 배터리 전환` · `L13 발전기 기동 실패`, 배터리 분당 1.13% 감소 |
| 부하 차단 판단 | 그룹별 kW · 차단 시 영향 · **끊었을 때 잔여 분** 산출, 차단 반영 확인 |
| SIEM 연동 | 경보가 syslog 로 Wazuh 매니저에 전달 — 환경 이상이 시스템 알림과 같은 화면에 |

> **전력은 대표 데이터센터 규모로 환산한다.** 실제 랩은 1.5kW 남짓이라 "총 38kW 중 학습 job
> 18kW 를 끊을 것인가" 같은 판단 실습이 성립하지 않는다. 환산을 숨기지 않으려고
> `measured_kw`(실측 원값)를 API 에 함께 노출한다. 환산의 입력인 사용률은 실측이므로,
> 3F 에 진짜 부하를 걸면 온도가 진짜로 오른다.

**환경 시뮬레이터 API** — `http://192.168.136.145:8010`

| 엔드포인트 | 용도 |
|---|---|
| `GET /state` | 층·아일 온습도, 전력, 경보, 부하 분석 전부 (UI 가 폴링) |
| `GET /assets` | 자산 대장 원본 — 배치도 데이터 모델이자 CMDB 기준 |
| `GET /shed` | 부하 그룹별 소비 + 차단 시 영향 + 끊었을 때 잔여 시간 |
| `POST /inject?fault=&target=` | 강사용 고장 주입 (10종) |
| `POST /shed?group=` | 부하 차단 — ENV-03 에서 학생이 내리는 판단 |
| `POST /reset` | 전부 원복 |

## 현재 상태

- [x] el34 → kt66 fork · 불필요 스택 제거 · 개명(116파일)
- [x] 16 컨테이너 기동 + 체인 검증
- [x] 근무자 에이전트 중립 스펙 + `agentctl` (bastion / hermes / claude)
- [x] GPU 존 — WireGuard 터널 + DGX Spark 편입 (10.20.50.10, Wazuh Active)
- [x] 환경 시뮬레이터 — 1F 시설 계통 + 경보 15종 + 부하 차단 판단
- [ ] 4층 시각화 UI
- [ ] 시나리오 34종을 `scenarios/*.yaml` 로 (`docs/시나리오_카탈로그.md` 의 선언 형식)
- [ ] 채점 (`assessor`/`provisioner` 활성화)

---

`README.el34-inherited.md` 는 el34 원본 문서다 — 이관 과정 참고용으로만 남겨 둔다.
