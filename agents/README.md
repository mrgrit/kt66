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
| `claude-code` | 호스트에 로그인된 `claude` 세션 | 구독 한도 안. 토큰당 0 |
| `mock` | 호출하지 않는다 | 0 |

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
동시에 여러 자리를 돌리려면 터미널을 그만큼 연다.

**`cd` 해서 직접 `claude` 를 치지 말 것.** Claude Code 는 환경에 `ANTHROPIC_API_KEY`
같은 변수가 있으면 구독보다 그것을 **먼저** 쓴다. 경고도 없다. `cc-session` 은 실행
직전에 `ANTHROPIC_*` · `CLAUDE_CODE_USE_*` 를 전부 걷어낸다 — 구독 자격증명은 환경이
아니라 디스크에 있으므로, 비우고 나야 구독 경로만 남는다. `./kt66.sh up` 도 그런 변수가
셸이나 `.env` 에 있으면 짚어 준다.

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
```
