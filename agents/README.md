# kt66 근무자 에이전트 — 런타임 중립 스펙

데이터센터에는 사람이 근무한다. kt66 에서는 그 자리를 **에이전트**가 채운다. 시설 담당,
네트워크 엔지니어, GPU 플랫폼 엔지니어, 서비스데스크, SOC 분석가, 운영 리드, 감사인 —
각자 맡은 층과 권한이 있고, 각자의 일상 작업(루프)을 돈다.

문제는 **에이전트 런타임이 하나가 아니라는 것**이다. bastion, Hermes Agent, Claude Code 는
잘하는 게 서로 다르다. 그래서 kt66 은 페르소나를 특정 런타임에 묶지 않는다. **중립 스펙으로
한 번 쓰고, 어댑터가 각 런타임 형식으로 렌더한다.**

```
      personas/*.md  loops/*.yaml  skills/*/SKILL.md      ← 한 번만 쓴다
                          │
                     roster.yaml                          ← 여기서 런타임을 고른다
                          │
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
   runtimes/bastion  runtimes/hermes   runtimes/claude     ← 어댑터가 렌더
        │                 │                 │
  .bastion/agents/    SOUL.md +        .claude/agents/
  BASTION.md          skills/ + cron/   CLAUDE.md
```

포털(UI)은 `agentctl` 한 곳만 호출한다. **UI 는 뒤에 어떤 런타임이 있는지 모른다.**

## 왜 페르소나 단위로 고르는가

전역 스위치가 아니라 `roster.yaml` 에서 **페르소나마다** 런타임을 지정한다.

같은 작업을 서로 다른 런타임에 시켜 결과를 비교하는 것 자체가 15주차 *"자동화 범위 설정"*
실습이 되기 때문이다. 시설 담당은 Hermes 로, SOC 분석가는 bastion 으로 돌려 놓고 어느 쪽이
어떤 종류의 일에 강한지 학생이 직접 본다.

## 런타임 비교 (실측 기준)

| | bastion | Hermes Agent | Claude Code |
|---|---|---|---|
| 출처 | mrgrit/bastion (el34 내장) | Nous Research, MIT | Anthropic |
| 정체성 파일 | `BASTION.md` | `SOUL.md` | `CLAUDE.md` |
| 페르소나 | `.bastion/agents/*.md` (frontmatter) | SOUL.md 단일 | `.claude/agents/*.md` |
| 스킬 | `SKILL.md` | `SKILL.md` + `DESCRIPTION.md` 계층 | `SKILL.md` |
| **루프/스케줄** | 없음 | **`cron/`** | — |
| 메모리 | experience / KG(sqlite) | `memories/` + `state.db` | — |
| 검증 | `verify.py` | `verification_evidence.db` | — |
| 샌드박스 | docker exec | `sandboxes/` (7종 백엔드) | — |
| 위임 | `orchestrator.py` | `delegation:` (config) | subagent |
| 가드레일 | 하네스 규칙(승인 게이트) | `tool_loop_guardrails:` | 권한 모드 |
| 외부 연동 | 자체 API | **`hermes acp`** (표준 프로토콜) | — |
| 모델 백엔드 | OpenAI 호환 | OpenAI 호환 (Ollama 가능) | **구독 CC 세션** (로컬 `claude`) |

**요약** — Hermes 가 루프·메모리·샌드박스·프로토콜에서 앞선다. bastion 은 다중 페르소나
하네스와 지식그래프가 강점이다. 12주(다역할 인시던트 대응)는 bastion, 15주(루프 엔지니어링)는
Hermes 가 유리하다. 그래서 둘 다 둔다.

## 모델은 어디서 오는가 — 과금되는 API 는 없다

kt66 이 쓰는 모델은 두 가지뿐이다. **랩 안의 GPU**(DGX Spark 의 Ollama)와, **이미
구독 중인 Claude Code 세션**이다. 토큰당 과금되는 API 는 카탈로그에 두지 않는다.

강의용 랩에서 이건 취향이 아니라 안전장치다. 학생 20명이 드롭다운을 눌러 보는 화면에
과금 모델이 하나 섞여 있으면, 실습이 끝난 뒤에 누군가는 청구서를 받는다 — 그것도 자기
것이 아닌 청구서를. **고를 수 없으면 고를 일도 없다.**

| 모델 키 | 실체 | 비용 |
|---|---|---|
| `local-small` · `local-reasoning` | 랩 GPU 의 Ollama | 0 |
| `cc-opus` · `cc-sonnet` · `cc-haiku` | 호스트에 로그인된 `claude` 세션 | 구독 한도 안. 토큰당 0 |
| `mock` | 호출하지 않는다 | 0 |

`cc-*` 셋은 **같은 구독**이다 — 과금이 달라지지 않는다. 다른 것은 세션을 띄울 때
넘어가는 `--model` 값뿐이다. 그래도 자리마다 고른다: 승인 판정과 초벌 분류에 같은
모델을 쓸 이유가 없고, 어느 자리에 무엇이 맞는지 학생이 직접 비교해 보는 것이 W15
실습이다.

| 모델 | `--model` | 맞는 자리 |
|---|---|---|
| `cc-opus` | `opus` | 승인 판정·설계·사후분석 — 한 번의 판단이 비싼 자리 |
| `cc-sonnet` | `sonnet` | 조사·감사처럼 읽을 것이 많은 자리. 기본으로 삼기 좋다 |
| `cc-haiku` | `haiku` | 분류·요약·초벌 정리 |

별칭은 설치된 `claude` 가 받는 것이어야 하고, **구독 플랜에 따라 거부될 수 있다**.
세션 안에서 `/model` 로 다시 바꿔도 된다 — roster 값은 그 자리의 기본값이다.

규칙은 주석이 아니라 **검사**다. `roster.yaml` 에 `api.anthropic.com` 같은 과금 호스트를
되돌려 놓으면 저장이 거부된다(`agentops/app.py` 의 `validate_all`) — 웹 화면이든 셸
편집이든 같은 문을 지난다.

### 자리에 세션을 하나 띄운다

`runtime: claude` 인 근무자는 새 API 계정이 아니라 **세션 하나**를 받는다.

```bash
./agents/cc-session                    # 자리 목록 + claude CLI·환경 상태
./agents/cc-session ops-lead           # 그 자리를 렌더하고 세션을 띄운다
./agents/cc-session --all              # 자리를 전부 렌더하고 실행 명령을 찍는다
```

세션은 자리마다 따로 돈다 — 렌더된 디렉터리(`runtimes/claude/rendered/<근무자>/`)가
그대로 작업 디렉터리이고, 거기 `CLAUDE.md` 와 `.claude/agents/<근무자>.md` 가 놓인다.
동시에 여러 자리를 돌리려면 터미널을 그만큼 연다. `roster.yaml` 에 적힌 모델은
`--model` 로 넘어가고, 목록 화면이 자리마다 무엇이 넘어가는지 보여 준다:

```
ops-lead              운영 리드          4F approver --model opus
compliance-auditor    컴플라이언스 감사인   4F L1       --model sonnet
```

`runtime: claude` 인데 `cc-*` 가 아닌 모델(예: `local-small`)이 배정돼 있으면 `--model`
없이 띄우고 이유를 한 줄로 말한다 — 무엇을 넘길지 모를 때 아무거나 넘기지 않는다.

**`cd` 해서 직접 `claude` 를 치지 말 것.** Claude Code 는 환경에 `ANTHROPIC_API_KEY`
같은 변수가 있으면 구독보다 그것을 **먼저** 쓴다. 경고도 없다. `cc-session` 은 실행
직전에 `ANTHROPIC_*` · `CLAUDE_CODE_USE_*` 를 전부 걷어낸다 — 구독 자격증명은 환경이
아니라 디스크에 있으므로, 비우고 나야 구독 경로만 남는다. `./kt66.sh up` 도 그런 변수가
셸이나 `.env` 에 있으면 짚어 준다.

## 사건이 나면 누가 움직이는가

오래도록 이 디렉터리는 **설계 산출물**이었다. 명단이 있고 페르소나가 있고 루프에
cadence 가 적혀 있었지만, 그 cadence 를 읽어 깨우는 것이 없었다. 수전이 끊겨도 화면에
경보만 뜨고 시설 담당은 아무것도 하지 않았다. `cc-runner` 가 그 빈 칸이다.

```bash
./agents/cc-runner              # 지금 경보를 한 번 훑고 해당하면 돌린다
./agents/cc-runner --watch      # 주기 폴링 (기본 30초)
./agents/cc-runner --dry-run    # 프롬프트만 찍는다. 세션도 상태도 남기지 않는다
./agents/cc-runner --tickets    # 티켓 목록
```

### 자기 일인지 어떻게 아는가

세 가지 답이 있고, 앞의 둘을 겹쳐 쓴다.

| | 방식 | 강점 | 약점 |
|---|---|---|---|
| ① | 루프의 `triggers.alarms` → 그 루프의 `owner` | 빠르고 싸고 재현된다 | **적어 둔 것만** 처리된다 |
| ② | 서비스데스크가 접수해 배정 | 모르는 사건·협업 사건을 받는다 | 세션이 한 단계 늘고 오배정 가능 |
| ③ | 전원에게 방송하고 각자 판단 (**쓰지 않는다**) | 미정의 사건에 가장 강하다 | 근무자 수만큼 세션. 아무도 안 잡거나 다 잡는다 |

**①이 먼저다.** 수전이 끊겼는데 서비스데스크에게 "누구를 부를까요"를 묻는 건 시간
낭비다. **②는 미정의 사건과 협업 사건에서 켜진다** — 아무 루프에도 안 걸린 경보가 있거나
여러 담당에 걸칠 때. 그때 서비스데스크가 사건을 읽고 배정표를 낸다.

무엇에 반응할지는 코드가 아니라 **루프 파일**에 적힌다. 그게 운영 판단이고, 학생이
고쳐야 하는 값이기 때문이다:

```yaml
triggers:
  alarms: [UPS_ONBATT, UPS_LOW, GEN_FAIL, PDU_OVERLOAD, ...]
```

### 협업과 승인

배정이 여럿이면 각자 세션을 돌리되 **티켓은 하나**를 공유한다. 뒤에 불린 사람은 앞사람의
소견을 읽고 시작하므로, 되풀이 대신 **더할 것과 동의하지 않는 지점**을 적는다. 소견이
둘 이상 모이면 `ops-lead`(autonomy `approver`)가 종합해 판정한다 — 승인·조건부·반려.

루프 게이트에 이미 "부하 제한은 gpu-platform-engineer 와 합의 후 승인 요청"이 적혀
있었다. 그 문장이 그림이 아니라 실제 절차가 되는 지점이다.

### 누가 무엇을 다뤘는지가 남는다

사건이 닫히면 러너가 **경험그래프**(`graph/experience.json`)에 참여 기록을 남긴다.

```json
{"id": "w.facility-engineer", "kind": "worker", "label": "시설 담당(전기/기계)",
 "floor": "1F", "zone": "ot", "team": "cooling-team"}

{"from": "w.facility-engineer", "to": "s.ups_onbatt", "type": "handled",
 "source": "20260905-141845.md", "role": "소견", "confidence": 1.0}
```

`roster.yaml` 은 `team` 옆에 "KPI 귀속과 경험그래프가 여기서 온다"고 적어 뒀는데, 정작
그래프에는 **사람이 없었다**. 자산·증상·원인·조치만 있으면 같은 증상이 다시 떴을 때
"이거 전에 누가 봤지"에 답할 수 없다. 배정을 좋게 만드는 것은 결국 그 기록이다.

그래프의 규율 두 가지를 그대로 지킨다.

- **모든 edge 는 `source` 를 갖는다.** 여기서는 **티켓 파일**이 출처다 — 열면 그 사람이
  실제로 무엇을 적었는지 볼 수 있다. `source` 없는 edge 는 콘솔이 저장을 거부한다.
- **겪은 것만 적는다.** 경보 34종을 미리 심지 않는다. 실제로 뜬 증상만 node 가 되고,
  이미 있는 node(`s.ups_onbatt` 등)는 재사용한다. 그게 "경험"그래프인 이유다.

역할도 함께 남는다(`접수`·`소견`·`판정`). 같은 사건에서 같은 사람이 같은 증상을 두 번
기록하지는 않는다.

쓰기는 **줄 단위 삽입**이다. 이 파일은 node 하나를 한 줄에 적고 묶음마다 빈 줄을 넣어
표처럼 읽게 돼 있는데, `json.dumps` 로 통째로 다시 쓰면 그 편집이 전부 사라진다(한 번
그렇게 뭉갰다). 읽기는 JSON 으로, 쓰기는 배열 끝에 줄을 끼우는 방식으로 한다. 쓰기 전에
파싱해 보고 망가졌으면 아예 쓰지 않는다.

### 지켜지는 것

- **한 사건에 세션 최대 5개** (`CC_RUNNER_MAX_SESSIONS`). 접수·전문가·승인을 합쳐서다.
  소견이 둘 이상 모일 참이면 승인자 몫 한 자리를 미리 뗀다 — 전문가로 예산을 다 쓰면
  판정 없는 티켓이 남고, 그건 L2 절차가 아니다. 모자란 배정은 티켓에 "미배정"으로 남는다.
- **L1·L2 는 상태를 바꾸지 않는다.** 프롬프트로도 말하고 **도구로도 막는다** — 세션에는
  `Read Glob Grep` 만 주어진다. 말만으로 지켜지는 게이트는 게이트가 아니다.
- **세션은 랩을 뒤지지 않는다.** 필요한 수치는 미리 프롬프트에 담는다. 직접 뒤지게 하면
  승인 게이트를 우회할 길이 생기고 재현도 안 된다.
- 티켓은 `agents/tickets/*.md`, **git 에 남는다.** 그래프의 `handled` edge 가 `source` 로
  티켓 파일명을 적는데 티켓이 저장소에 없으면 그 provenance 를 아무도 검증할 수 없다 —
  규율 ① 이 무너진다. 감사인의 루프 이름도 `evidence-collection` 이다. 러너의 내부
  상태(`.state.json`)만 뺀다.

### 지금의 한계 (알고 쓰는 단순화)

- **활성 경보 전부를 하나의 사건으로 본다.** 이 랩의 고장은 대개 하나에서 연쇄하므로
  (수전 상실 → UPS 전환 → 냉각수 펌프 정지) 사람의 인식과 맞는다. 무관한 두 고장을
  동시에 주입하면 한 티켓에 섞인다.
- **cadence 는 아직 돌지 않는다.** 지금 깨우는 것은 경보뿐이다. 정기 점검 루프
  (`*/15 * * * *`)를 실제로 돌리려면 스케줄러가 따로 필요하다.
- 티켓은 NOC 화면에 아직 나오지 않는다. 파일로만 남는다.
- 경험그래프에 남는 것은 **참여 사실**(누가·어느 증상·어떤 역할)까지다. 소견의
  내용에서 새 원인·조치 node 를 뽑는 것은 아직 사람이 한다.

## 자율성 등급

loop-engineering 의 등급을 그대로 쓴다. `roster.yaml` 의 `autonomy` 값이다.

| 등급 | 의미 | 상태 변경 |
|---|---|---|
| `L1` | 보고 전용 — 관찰하고 티켓만 만든다 | ❌ |
| `L2` | 승인 후 실행 — 계획을 내고 `ops-lead` 승인을 받아 실행 | ✅ (승인 필요) |
| `L3` | 무인 — 사전 승인된 런북 범위 안에서 스스로 실행 | ✅ |
| `approver` | 승인 전담 — 스스로 실행하지 않고 L2 요청을 판정 | ❌ |

**L3 는 런북이 등록된 작업에만 허용한다.** 런북 없는 작업을 L3 로 돌리는 것은 금지다 —
되돌리기 경로가 정의되지 않은 자동화이기 때문이다.

## 사용법

```bash
./agentctl list                      # 명단 + 각자의 런타임/층/자율성
./agentctl render <persona>          # 지정 런타임 형식으로 렌더
./agentctl render --all
./agentctl runtime <persona> hermes  # 런타임 교체 (roster.yaml 갱신)
./agentctl diff <persona>            # 두 런타임 렌더 결과 비교 (실습용)

./cc-session                         # 구독 CC 세션 자리 목록
./cc-session ops-lead                # 그 자리에 세션 하나 (과금 변수 걷어내고 실행)

./cc-runner                          # 경보를 훑어 담당자를 부른다
./cc-runner --dry-run                # 프롬프트만 본다 (세션 안 씀)
./cc-runner --watch                  # 수업 중에는 이걸 켜 둔다
./cc-runner --tickets                # 사건 티켓 목록
```

## 파일

```
agents/
  roster.yaml          근무자 명단 — 층·역할·런타임·자율성·루프 바인딩
  personas/*.md        런타임 중립 페르소나 (frontmatter + 본문)
  loops/*.yaml         일상 작업 — cadence·트리거·예산·승인 게이트
  skills/*/SKILL.md    3런타임 공통 (같은 관례를 쓴다)
  runtimes/            어댑터
  agentctl             렌더러 + 제어 CLI
  cc-session           구독 Claude Code 세션 실행기 (과금 경로 차단 포함)
  cc-runner            경보 → 접수 → 배정 → 소견 → 판정. 사건이 나면 사람을 부른다
  tickets/             사건 티켓 (실행 산출물 — git 에 넣지 않는다)
```
