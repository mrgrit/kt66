# KT66 조직 하네스 및 상시 루프

처음 설정하거나 운영 절차가 필요하면 [한국어 상세 운영 매뉴얼](../docs/AGENT-OPERATIONS.ko.md)을 읽습니다. 이 문서는 구현과 운영 명령의 짧은 요약입니다.

## 현재 실행 경로

웹의 회사·부서·팀·근무자·루프·하네스 저장은 원본 파일을 갱신한 뒤 자동으로 하네스를 생성합니다. 생성 실패는 저장 성공과 구분하여 HTTP 409로 표시합니다. 다음 작업은 원본을 다시 컴파일하여 최신 정책을 사용합니다.

- 원본: company.yaml, departments.yaml, teams.yaml, roster.yaml, harness.yaml, personas/*.md, loops/*.yaml
- 역할 스킬: personas의 `skills` 목록 → native/.agents/skills/<이름>/SKILL.md.
  원본 해시와 불변 사본을 보존하고 정기·사용자 업무에서 `skill_read`로 필요할 때 읽는다.
- 생성: harness_compiler.py
- 버전: runtimes/<runtime>/versions/<worker>/<hash>/
- 활성 위치: runtimes/<runtime>/rendered/<worker> (불변 버전을 가리키는 상대 링크)
- 웹 적용 상태: /api/activation, /api/loop-status
- 상시 실행: loop_engine.py / 사용자 systemd kt66-runner.service
- 호환 진입점: cc-runner --watch, cc-runner --status
- 세션 실행: session_cli.py / 수동 Claude 작업은 cc-session

상속 순서는 공통 정책 → 부서 → 팀 → 근무자입니다. 상위 deny는 해제할 수 없고, 명시적인 ask→allow 재정의는 허용합니다. 실제 작업 루프의 낮은 자율성은 실행 권한을 추가로 제한합니다. 회사 원칙, 부서 책임, 팀 KPI, 근무자 역할/자산, 루프 원문과 정책을 같은 생성물에 포함합니다.

직무 상한은 `roster.yaml`의 `security_role`과 `harness.yaml`의 `security.roles`로
정합니다. `authorization.py`가 도구 목록·실제 호출·작업 배정·임시 역할·대화 승인·
변경안 적용에서 이를 검사합니다. `allow`나 ‘항상 허용’으로 직무 밖 도구를 열 수
없습니다. 원본 정책이 바뀌면 실행 중 세션을 차단하고 새 하네스가 필요합니다.
직무나 대상 범위가 달라지면 이전 대화 승인도 무효입니다.
설계·잔여 위험·실습은 [위험 평가 문서](../docs/agent-security-design.ko.md)를 참고하세요.

## 새 CLI 세션과 도구

각 작업은 기존 claude.ai 또는 ChatGPT 로그인으로 새 Claude Code/Codex CLI 세션을 시작합니다. API 키 환경, 다른 모델 엔드포인트, 직접 모델 API fallback을 사용하지 않습니다.

자동 작업은 생성된 HARNESS.md를 CLI system/developer 지침으로 명시적으로 전달하고 그 불변 생성 디렉터리를 작업 위치로 사용합니다. 임의 셸은 노출하지 않으며 전용 MCP 서버의 도구를 제공합니다. Codex의 해당 서버에 대한 전송 승인은 미리 설정하되 실제 도구 권한은 서버에서 매 호출 강제합니다. 참고: https://learn.chatgpt.com/docs/config-file/config-reference

현재 제공 범위:

- 가상 설비 상태·경보·고장 조회
- 실제 Wazuh 로그 원본 스냅샷 및 해시 보존
- 실제 Docker 상태/자원 및 실습 방화벽 nft 규칙 읽기
- 증적 티켓, 선언된 회차 상태, 업무/승인 상태 조회
- 설정된 역할에 따른 업무 전달
- 담당 자산의 정확한 활성 가상 고장 1건 해제: L1 금지, L2 승인 필요, L3도 도구 권한 필요
- 독립 승인자의 판단 후 서버가 원 요청자의 권한으로 실행·재조회
- 승인/실행 뒤 원 담당자에게 사후검증 작업 자동 생성

정기 루프에는 실제 서비스 중단·방화벽 변경·데이터 삭제 도구를 제공하지 않습니다.
사용자 업무 요청의 정적 홈페이지와 WAF 규칙은 담당 직무가 검증된 변경안을 준비하고,
별도 검토와 사람의 적용 승인을 거쳐 실행기가 반영합니다. 설정에 권한 이름이 있다는
이유만으로 아직 없는 도구가 생기지는 않습니다. 필요한 증거가 없으면 미검증으로 남깁니다.

## 루프와 상태

15개 루프의 cadence와 triggers를 읽습니다. 평시 반복 감시 7개는 `monitor.mode: adaptive`로 동작합니다. 코드 점검의 기본 간격은 10분이며 변화·이상 지속 시 5분 → 2분 → 1분으로 줄어듭니다. 정상 관측이 2회 연속 이어질 때마다 한 단계씩 10분으로 복귀합니다. 최초 정상 기준 수집과 변화 없는 점검에는 모델을 호출하지 않습니다. 사건의 정적 소유권은 루프 설정으로 정하고, 미배정 관측은 execution.dispatcher의 근무자에게 넘깁니다. 그 근무자는 생성 하네스를 바탕으로 업무를 배정합니다. 시나리오별 정답 조치 enum과 특정 보안 시험 전용 실행은 사용하지 않습니다.

SOC는 평시/특이 사건의 `siem-alert-triage`와 종합 보고의 `soc-daily-risk-review`를
분리합니다. 종합 보고는 `execution.timezone=Asia/Seoul`에서 `0 9,18 * * *`, 하루 두 번입니다.
조회 질문·개별 사건에는 필요한 스킬만 읽고, 종합 분석 스킬을 자동으로 펼치지 않습니다.

SQLite 큐는 실행 예약, 결과, 재시도, 승인 대기, 재시작 복구를 보존합니다. 같은 근무자를 동시에 실행하지 않습니다. 승인/사후검증은 우선 처리하고, 평시/사건 작업은 번갈아 처리합니다. 중단 후에는 설정된 짧은 구간에서 최신 주기 하나로 합쳐 재개하며 하루치 작업을 몰아서 재생하지 않습니다. 성공 결과가 이미 저장된 중단 작업은 그 결과를 복원합니다.

- tickets/loop-engine.sqlite3: 작업 큐와 세션 시도
- tickets/worker-memory/: 직전 회차 결과/증거 연결
- tickets/cycle-state/: 루프에 선언된 지속 상태
- tickets/approvals/: 승인 요청·판정·집행·검증
- evidence/loop-*/: 작업 입력, 로드한 하네스 버전/해시, 실제 도구 영수증, CLI 결과

completed는 작업 회차 종료를 뜻하며 모든 KPI나 모델 서술의 사실성을 보증하지 않습니다. 실제 조치 성공은 서버의 재조회 결과와 도구 영수증으로 확인합니다. 로그 일부만 보고 과거 전체 가동률이나 비인가 접근 0건을 확정하면 안 됩니다.

## 사용량과 운영

harness.yaml의 execution에서 poll_seconds, max_concurrent_sessions, max_attempts, retry_backoff_sec, quota_backoff_sec, daily_session_limit, max_catchup_minutes를 설정합니다. daily_session_limit는 최근 24시간 세션 시도 상한이며 null이면 별도 로컬 상한이 없습니다. 구독 자체의 제한은 항상 적용되고 제한 응답 시 해당 런타임 전체가 재호출을 미룹니다. API로 우회하지 않습니다.

기존에는 평시 반복 감시만 하루 864회 AI 실행을 예약했습니다. 이제 이 5개 루프의 정상·무변화 점검은 코드로 수행하며 AI 세션을 생성하지 않습니다. 별도 일정의 방화벽 검토·GPU 쿼터 검토·백업·출입·전력 점검 13회와 SOC 종합 보고 2회로 합계 15회/일, 주간 감사는 별도입니다. 사건·승인·업무 전달·재시도는 추가될 수 있습니다.

`execution.adaptive`의 `intervals_sec`, `stable_samples`, `model_cooldown_sec`, `incident_review_sec`가 간격과 재호출을 제어합니다. 일반 변화의 AI 요청은 현재 점검 간격을 따르되 루프당 최소 60초의 중복 방지 간격을 두며, 동일한 지속 장애는 30분 후 재검토합니다. 긴급 사건 요청은 기존 경로로 즉시 전달되고 같은 관측으로 AI를 중복 호출하지 않습니다. 진행 중 작업 때문에 미룬 변경은 저장했다가 다시 판단합니다.

`monitor_state` 테이블은 비교 기준, 현재 간격, 다음 점검, 호출/생략 수와 사유를 보존합니다. 재시작해도 초기화되지 않으며, 전환 당시 오래된 주기 대기 작업은 `superseded`로 기록하고 새 관측으로 대체합니다. 관제 화면의 적응형 점검 영역에서 각 루프의 현재 정책과 판정을 확인할 수 있습니다. `max_tokens`는 여전히 CLI의 강제 토큰 중단 한도가 아니며, 이번 절감은 불필요한 세션 자체를 시작하지 않는 방식입니다.

서비스:
- systemctl --user status kt66-runner.service
- systemctl --user stop kt66-runner.service
- systemctl --user start kt66-runner.service
- journalctl --user -u kt66-runner.service

## 검증 범위 (2026-09-15)

- 조직 원칙/부서/KPI/권한 변경이 생성물과 버전에 반영됨.
- Claude와 Codex 새 세션의 실제 MCP 호출 확인.
- 분리된 동일 역할의 env_read=deny 설정에서 실제 도구 목록/호출 변화 확인.
- inference-sla-watch가 10:15, 10:20 UTC 등 여러 예정 주기에서 별도 세션으로 실행.
- 가상 crac_fail/crac-01: 관측→자율 요청→독립 승인→실제 해제→경보 소멸→사후검증/상태 갱신.
- 실제 SIEM 원본 로그 읽기/증거 보존 확인.
- 에이전트 회귀 테스트 54개 및 포털 inventory 테스트 3개 통과. 구현은 `b5d6ec2`로 커밋·푸시했으며 운영 중 경험그래프 변경은 별도로 보존.

72개 전체 시나리오, 장기간 가용성, 모든 인프라 조치, 모든 KPI 계산은 검증한 것이 아닙니다. 모델 서술의 관측 범위 초과 추정은 여전히 검토가 필요합니다. 독립 실행 증거와 모델 의견을 구분합니다.


## 5F 연구소와 xOC 실행 정책

신규 3명은 기존 ChatGPT 구독 로그인으로 Codex CLI를 사용합니다. 연구원은 Astra/high, 평가원은 Sol/medium, AI 관제원은 Luna/medium입니다. 연구원만 공개 웹 검색을 사용할 수 있으며, 운영 셸·인프라 도구는 세 역할 모두 사용할 수 없습니다. 모델과 노력 수준은 `roster.yaml`에서 변경합니다.

`harness.yaml`의 `execution.worker_daily_limits`는 최근 24시간 자동 작업을 연구 1회, 평가 3회, 관제 4회로 제한합니다. 사용자 요청은 이 제한에서 제외되지만 기존 업무 요청 예산을 따릅니다. 평가 작업 1건은 기준/후보의 독립 모델 호출 2회를 포함하므로 작업 수와 모델 호출 수를 구분해야 합니다.

`agent-risk-watch`는 환경 시뮬레이터가 응답하지 않아도 로컬 실행 증거를 점검합니다. xOC 보류는 새 세션·다음 도구 호출·이전에 승인된 변경 실행을 막습니다. 이미 진행 중인 추론/원격 작업을 강제로 종료하지 않으며, 상태 파일이 손상되면 보류를 무시하지 않고 오류로 중단합니다.

[연구·평가·관제 절차와 제한](../docs/AI-RESEARCH-XOC.ko.md)을 함께 읽으세요.
