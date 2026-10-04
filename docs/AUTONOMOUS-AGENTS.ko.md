# 실제 작업을 확인하며 배우는 자율 에이전트

KT66의 교육 목표는 전문가의 업무 방식을 자연어 파일로 옮기고, **같은 문제에서 작업 순서·증거·결과가 어떻게 달라지는지** 확인하는 것입니다. 캐릭터 이동이나 ‘완료’ 표시는 실행 사실 전체를 증명하지 않습니다. NOC의 **AI 에이전트 관제 → 실행 선택 → 실제 작업 확인**에서 조회 영수증, 읽은 스킬, 코드 검사 결과를 함께 보세요.

## 표준 파일과 KT66 설정의 관계

역할 원본은 `agents/native/.claude/agents/<역할>.md`입니다. 이전 `agents/personas/`는 사용하지 않습니다. 공통 스킬 원본은 `agents/native/.agents/skills/<이름>/SKILL.md`입니다. 근무자 운영 화면의 R&R·스킬 연결과 원문 편집은 이 파일들을 수정합니다.

```text
agents/
├── native/                             # 교육용 원본
│   ├── AGENTS.md                       # 사용자 업무 공통 원칙
│   ├── CLAUDE.md                       # @AGENTS.md로 같은 원칙 참조
│   ├── .claude/agents/<역할>.md         # name/description/model/skills + 역할 본문
│   └── .agents/skills/<스킬>/
│       ├── SKILL.md                    # 언제·어떤 순서·어디서 멈출지
│       ├── references/                # 필요할 때 읽는 상세 자료
│       └── scripts/                   # 결정적 검사 코드
├── roster.yaml                        # 명단·구독 CLI·모델·담당 자산
├── teams.yaml / departments.yaml       # 실제 조직 R&R·협업·KPI
├── harness.yaml                       # 코드가 강제하는 직무 상한·승인·예산
├── loops/<업무>.yaml                   # KT66 예약 주기·상태·업무 목표
├── runtimes/<runtime>/versions/…/      # 정기 작업의 불변 실행 사본
└── evidence/<실행>/harness/            # 사용자 업무의 실행 사본
```

실행 사본에는 `.claude/agents/*.md`, `.codex/agents/*.toml`, `.claude/skills/`, `.agents/skills/`가 생성됩니다. Claude는 역할 Markdown을 `--agents/--agent`로 전달받고, Codex는 역할 TOML의 `developer_instructions`를 실행 입력으로 전달받습니다. 임시 작업 디렉터리에서 시작해 동일 지침의 자동 탐색·중복 적재를 피합니다. 모델과 권한의 최종 기준은 `roster.yaml`과 서버 정책입니다. 원본 Markdown의 `tools`를 늘리는 것만으로 권한이 생기지 않습니다.

이는 Claude/Codex의 표준 역할·스킬 형식을 사용하면서 KT66이 세션과 업무 큐를 관리하는 방식입니다. `teams.yaml`·`loops/*.yaml`은 제품 공통 표준이 아니라 KT66 조직·일정 설정입니다. Claude Agent Teams나 Codex 내부 재귀 위임을 자동으로 켜는 설정이라고 설명하지 마세요. 조사·개발·검토는 배정된 역할별 **별도 세션**으로 실행하고, 요청 ID와 산출물로 연결합니다.

Hermes는 표준 스킬 내보내기를 지원합니다.

```bash
python3 agents/agentctl render network-engineer --runtime hermes
# 출력: agents/runtimes/hermes/rendered/network-engineer/
# skills/<이름>/ 폴더를 Hermes의 ~/.hermes/skills/에 배치
```

Hermes 자동 실행·구독 로그인·MCP 연결은 현재 연동되어 있지 않습니다. 내보내기의 `kt66-reference-loops/`는 참고 자료이며 Hermes cron 설정이 아닙니다. KT66 도구를 사용하는 스킬은 권한 게이트웨이 연결이 있어야 동작합니다. 원본·생성물의 구분을 유지하고 학생이 실행 사본을 직접 고치지 않도록 지도하세요.

표준 근거: [Claude 역할](https://code.claude.com/docs/en/sub-agents), [Claude 스킬](https://code.claude.com/docs/en/skills), [Codex 스킬](https://learn.chatgpt.com/docs/build-skills), [Codex 역할](https://learn.chatgpt.com/docs/agent-configuration/subagents), [Hermes 스킬](https://hermes-agent.nousresearch.com/docs/developer-guide/creating-skills).

## 필요할 때만 상세 절차를 읽기

기본 컨텍스트에는 역할과 배정 스킬의 이름·설명을 제공합니다. 본문·참고 문서는 `skill_read`로 필요한 것만 읽습니다. 실행 영수증에는 파일 경로와 SHA-256이 남습니다. 일상 질문에 IP 위험 평가 전체를 주입할 필요가 없습니다.

| 상황 | 실제 적용 |
|---|---|
| SOC 평시 무변화 | 기본 10분의 코드 점검. 무변화이면 모델 세션 생략 |
| 오늘 경보·최근 1시간 조회 | `siem-period-analysis` → `siem_search` |
| 특정 사건 조사 | `soc-incident-investigation`, 기간 조회가 필요하면 조회 스킬도 사용 |
| 하루 두 번 종합 분석 | 기존 `soc-daily-risk-review` 일정과 `ip-risk-investigation` |
| 네트워크 정기/접속 점검 | `network-diagnosis` → `network_probe` 1회 |
| 정책 검토·변경안 요청 | 해당 시점에 `network-policy-audit` 또는 `network-change-review` |

`siem_search`와 `network_probe`는 필수 스킬 로드 영수증이 없으면 **코드가 차단**합니다. 잘못된 호출은 이유와 필요한 스킬을 반환해 다음 호출에서 복구할 수 있습니다. 본문을 읽었다는 사실이 모든 자연어 절차 준수를 보증하지는 않습니다. 판단 품질과 범위 확대 여부는 검토 대상입니다.

역할에 연결한 스킬·참고 자료·역할 변경은 정책 버전 해시에 반영되며 기존 세션의 다음 도구 접근을 차단합니다. 사용자 업무의 공통 지침과 자동 배정 스킬도 실행 사본의 버전으로 보존되지만, 원본 편집 내용은 다음 세션부터 반영됩니다. 스킬 수정 실습은 진행 중인 작업이 없는 때에 하고 새 요청으로 비교하세요. 토큰 표시는 CLI 처리량이며 구독 차감액이 아닙니다.

## 네트워크 엔지니어의 기본 동작

`network_probe`는 임의 명령이나 IP를 입력받지 않습니다. FW·IPS·WAF·공격자 컨테이너의 상태, 라우트, 링크, FW/IPS 정책과 지정 HTTP 응답을 수집합니다. 저장소 기본 정책, 웹 DNAT, 출처 IP 보존에 필요한 호스트 브리지 설정을 코드로 비교합니다. 원문은 `network-<ID>.json`, 모델에는 항목별 검사와 증거 참조를 전달합니다.

- `no_fault`: **현재 검사한 항목과 지정 경로**에서 이상 없음.
- `fault_observed`: 실패한 검사 존재. 증상이며 단독으로 근본원인을 확정하지 않음.
- `insufficient_evidence`: 수집 불가·누락. 정상이나 장애로 바꾸어 말하지 않음.

‘방화벽 고장’이라는 티켓 문장은 가설입니다. 정상 증거에서 원인을 만들어내지 않습니다. 카운터 증가·핸들 변화는 구성 드리프트 해시에서 제외합니다. 물리 광모듈, Cisco/Junos BGP/OSPF, Batfish·pyATS, 전체 정책 의미 검증은 구현된 검사라고 주장하지 않습니다. 사용자망·모든 서비스·과거 시점의 정상 여부도 기본 HTTP 한 번으로 증명할 수 없습니다.

네트워크 담당자에게 디스크를 물어도 시스템 권한은 생기지 않습니다. 직무 경계가 승인보다 우선합니다. 네트워크 임의 셸·SSH·라우팅 변경 도구는 제공하지 않습니다. 현재 WAF 변경은 기존 변경안 생성·독립 검토·승인 경로를 사용합니다. 범용 네트워크 MOP 실행·장비별 자동 롤백 타이머는 **미구현**이며 스킬 문서만으로 활성화하지 않습니다.

## 무엇으로 검증하나요?

1. 같은 범위에서 정상 요청과 잘못된 원인 주장을 각각 보냅니다.
2. 계획 → 필요한 스킬 로드 → 실제 관측 → 검사 → 결과의 영수증을 확인합니다.
3. 수집 실패·권한 밖 요청은 보류·담당자 안내로 끝나는지 확인합니다.
4. 실제 조회 없는 `request_finish`·`work_status`만으로 점검 완료 근거가 생기지 않는지 확인합니다.
5. 스킬 한 가지를 바꾸고 새 요청으로 절차·관측 범위·출력·도구 횟수·토큰을 비교합니다.

회귀 검증: `python3 -m unittest discover -s agents/tests -p test_network_evidence.py -v`.
정상 대조군, 틀린 티켓 주장, 라우트/실행 상태 이상, 브리지 필터 회귀, HTTP 타임아웃, 수집 누락, 카운터 증가, 필수 스킬 게이트와 직무 경계를 포함합니다. 테스트 픽스처의 결과와 실제 운영망 검사 결과는 구분해 기록하세요.

실제 변경·검증 결과는 [검증 기록](AUTONOMOUS-VALIDATION.ko.md), Windows 실습망은 [설치 안내](WINDOWS-ENDPOINT.ko.md)를 참고하세요.
