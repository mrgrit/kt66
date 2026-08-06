"""kt66 환경 시뮬레이터 — 물리 모델.

핵심 원칙: **사용률은 실측이다.** 컨테이너 CPU 와 GPU 상태를 읽어 온다. 그래서 학생이
3F 에 부하를 걸면 온도가 진짜로 오르고, 1F 냉동기를 죽이면 그 열이 실제로 갈 곳을 잃는다.
시설 계통(전력·냉방·소방·물리보안)만 가상이다.

전력은 **대표 데이터센터 규모로 환산**한다(assets.yaml 주석 참조). 실제 랩은 1.5kW 남짓이라
"총 38kW 중 학습 job 18kW 를 끊을 것인가" 같은 판단 실습이 성립하지 않기 때문이다.
환산을 숨기지 않으려고 measured_kw(실측 원값)를 함께 노출한다.

열 모델은 1차 미분방정식이다:

    dT/dt = (발열 - 냉방 + 외기유입) / 열용량

정확한 CFD 가 목적이 아니다. **원인과 결과가 손에 잡히게 이어지는 것**이 목적이다.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

# ── 고장 주입 ────────────────────────────────────────────────────────
FAULTS = {
    "utility_fail": "수전 상실 — UPS 배터리로 전환",
    "generator_fail": "비상 발전기 기동 실패",
    "chiller_fail": "냉동기 정지 — 모든 CRAC 의 냉방 능력 상실",
    "crac_fail": "항온항습기 개별 정지",
    "pdu_overload": "PDU 과부하 — 차단기 트립",
    "smoke": "연기 감지",
    "door_forced": "출입문 강제 개방",
    "door_held": "출입문 장시간 열림",
    "cctv_offline": "CCTV 신호 상실",
    "humidity_drift": "습도 이상",
}


@dataclass
class AisleState:
    """아일 단위 열 상태. 냉방은 랙이 아니라 아일에 작용한다."""
    aisle: str
    floor: str
    temp_c: float = 22.0
    humidity_pct: float = 45.0
    it_kw: float = 0.0
    cooling_kw: float = 0.0
    crac_ids: list[str] = field(default_factory=list)


@dataclass
class PowerState:
    utility_ok: bool = True
    on_battery: bool = False
    generator_running: bool = False
    generator_failed: bool = False
    outage_started: float | None = None
    ups_charge_kwh: float = 12.0
    ups_capacity_kwh: float = 12.0
    total_kw: float = 0.0
    measured_kw: float = 0.0
    drain_pct_per_min: float = 0.0
    pdu_load: dict[str, float] = field(default_factory=dict)


class Simulator:
    def __init__(self, assets: dict):
        self.assets = assets
        b = assets["building"]
        self.thermal_mass = float(b.get("thermal_mass_kwh_per_c", 0.5))
        self.ambient = float(b.get("ambient_c", 22.0))
        self.target = float(b.get("target_c", 22.0))
        self.time_scale = float(b.get("time_scale", 1.0))

        self.faults: dict[str, set[str]] = {k: set() for k in FAULTS}
        self.shed: set[str] = set()          # 차단된 부하 그룹
        self.events: list[dict] = []
        self.active_alarms: dict[str, dict] = {}
        self.last_tick = time.time()

        self.aisles: dict[str, AisleState] = {}
        for rack in assets.get("racks", []):
            a = rack["aisle"]
            self.aisles.setdefault(a, AisleState(aisle=a, floor=rack["floor"],
                                                 temp_c=self.target))
        for crac in assets["facility"].get("crac", []):
            if crac["aisle"] in self.aisles:
                self.aisles[crac["aisle"]].crac_ids.append(crac["id"])

        ups = assets["facility"]["ups"][0]
        cap = float(ups["battery_kwh"])
        self.power = PowerState(ups_capacity_kwh=cap, ups_charge_kwh=cap)
        self.asset_kw: dict[str, float] = {}
        self.asset_util: dict[str, float] = {}
        self._rack_kw: dict[str, float] = {}

    # ── 고장 주입 ───────────────────────────────────────────────────
    def inject(self, fault: str, target: str = "*", clear: bool = False) -> dict:
        if fault not in FAULTS:
            raise ValueError(f"알 수 없는 고장: {fault} (가능: {', '.join(FAULTS)})")
        if clear:
            self.faults[fault].discard(target)
            self._event("info", f"고장 해제: {FAULTS[fault]} ({target})",
                        fault=fault, target=target, cleared=True)
        else:
            self.faults[fault].add(target)
            self._event("inject", f"고장 주입: {FAULTS[fault]} ({target})",
                        fault=fault, target=target)
        return {"fault": fault, "target": target, "cleared": clear,
                "active": sorted(self.faults[fault])}

    def _faulted(self, fault: str, target: str) -> bool:
        s = self.faults[fault]
        return "*" in s or target in s

    def clear_all(self) -> dict:
        n = sum(len(v) for v in self.faults.values()) + len(self.shed)
        for k in self.faults:
            self.faults[k].clear()
        self.shed.clear()
        self.power.ups_charge_kwh = self.power.ups_capacity_kwh
        self.power.generator_failed = False
        self.power.outage_started = None
        self._event("info", f"모든 고장·부하차단 해제 ({n}건)")
        return {"cleared": n}

    # ── 부하 차단 (ENV-03 의 학생 판단) ───────────────────────────────
    def shed_load(self, group: str, restore: bool = False) -> dict:
        groups = self.assets.get("shed_groups", {})
        if group not in groups:
            raise ValueError(f"알 수 없는 부하 그룹: {group} (가능: {', '.join(groups)})")
        if restore:
            self.shed.discard(group)
            self._event("info", f"부하 복구: {groups[group]['name']}", shed_group=group)
        else:
            self.shed.add(group)
            self._event("action", f"부하 차단: {groups[group]['name']} — {groups[group]['impact']}",
                        shed_group=group)
        return {"group": group, "restored": restore, "shed": sorted(self.shed)}

    # ── 한 틱 ──────────────────────────────────────────────────────
    def tick(self, util: dict[str, float]) -> None:
        """util: 자산 id -> 사용률(0.0~1.0). 실제 컨테이너/GPU 에서 읽어 온 값."""
        now = time.time()
        elapsed = max(min(now - self.last_tick, 120.0), 0.1) * self.time_scale
        self.last_tick = now
        dt_h = elapsed / 3600.0
        self.asset_util = dict(util)

        self._compute_power(util, dt_h)
        self._compute_thermal(dt_h)
        self._evaluate_alarms()

    # 전력 ------------------------------------------------------------
    def _compute_power(self, util: dict[str, float], dt_h: float):
        rack_kw: dict[str, float] = {}
        self.asset_kw.clear()
        measured_w = 0.0

        for a in self.assets["it_assets"]:
            u = max(0.0, min(util.get(a["id"], 0.0), 1.0))
            if a.get("shed_group") in self.shed:
                kw = 0.0                                   # 차단된 부하
            else:
                idle, rated = float(a["idle_kw"]), float(a["rated_kw"])
                kw = idle + (rated - idle) * u
            self.asset_kw[a["id"]] = kw
            rack_kw[a["rack"]] = rack_kw.get(a["rack"], 0.0) + kw
            # 실측 원값 — 환산 전. 명시된 자산만 집계한다(나머지는 컨테이너라 미미).
            if "measured_idle_w" in a:
                mi, mm = float(a["measured_idle_w"]), float(a["measured_max_w"])
                measured_w += mi + (mm - mi) * u

        pdu_load = {}
        for pdu in self.assets["facility"]["pdu"]:
            load = rack_kw.get(pdu.get("rack", ""), 0.0)
            if self._faulted("pdu_overload", pdu["id"]):
                load = float(pdu["capacity_kw"]) * 0.98    # 트립 직전
            pdu_load[pdu["id"]] = load
        self.power.pdu_load = pdu_load
        self.power.total_kw = sum(pdu_load.values())
        self.power.measured_kw = measured_w / 1000.0
        self._rack_kw = rack_kw

        # 수전 / 발전기 / UPS
        p = self.power
        p.utility_ok = not self._faulted("utility_fail", "utility-01")
        gen = self.assets["facility"]["generator"][0]

        if p.utility_ok:
            if p.on_battery or p.generator_running:
                self._event("info", "수전 복구 — 상용전원 전환, UPS 충전 시작")
            p.on_battery = False
            p.generator_running = False
            p.generator_failed = False
            p.outage_started = None
            p.ups_charge_kwh = min(p.ups_capacity_kwh,
                                   p.ups_charge_kwh + p.ups_capacity_kwh * 0.05 * dt_h * 60)
        else:
            if p.outage_started is None:
                p.outage_started = time.time()
                p.on_battery = True
                self._event("alarm", "수전 상실 — UPS 배터리로 전환")
            # 발전기는 지연 후 기동한다. generator_fail 이 주입돼 있으면 실패한다.
            elapsed_s = (time.time() - p.outage_started) * self.time_scale
            if elapsed_s >= float(gen["start_delay_s"]):
                if self._faulted("generator_fail", gen["id"]):
                    if not p.generator_failed:
                        p.generator_failed = True
                        self._event("alarm", f"비상 발전기 기동 실패 — 배터리만 남았다 ({gen['id']})")
                elif not p.generator_running:
                    p.generator_running = True
                    self._event("info", f"비상 발전기 기동 — 부하 인수 ({gen['id']})")

            if p.generator_running:
                p.on_battery = False
                p.ups_charge_kwh = min(p.ups_capacity_kwh,
                                       p.ups_charge_kwh + p.ups_capacity_kwh * 0.03 * dt_h * 60)
            else:
                p.on_battery = True
                p.ups_charge_kwh = max(0.0, p.ups_charge_kwh - p.total_kw * dt_h)

        # 분당 감소율 — 캡처의 "분당 5.4% 감소"
        if p.on_battery and p.ups_capacity_kwh > 0:
            p.drain_pct_per_min = p.total_kw / p.ups_capacity_kwh * 100.0 / 60.0
        else:
            p.drain_pct_per_min = 0.0

    @property
    def ups_runtime_min(self) -> float:
        load = max(self.power.total_kw, 0.01)
        return self.power.ups_charge_kwh / load * 60.0

    def runtime_if_shed(self, group: str) -> float:
        """이 그룹을 끊으면 잔여 몇 분인가 — ENV-03 판단의 근거."""
        drop = sum(kw for aid, kw in self.asset_kw.items()
                   if self._group_of(aid) == group)
        load = max(self.power.total_kw - drop, 0.01)
        return self.power.ups_charge_kwh / load * 60.0

    def _group_of(self, asset_id: str) -> str | None:
        for a in self.assets["it_assets"]:
            if a["id"] == asset_id:
                return a.get("shed_group")
        return None

    # 열 --------------------------------------------------------------
    def _compute_thermal(self, dt_h: float):
        chiller_down = any(self._faulted("chiller_fail", c["id"])
                           for c in self.assets["facility"]["chiller"])
        # 실제 DC 에서 CRAC 은 보통 UPS 를 타지 않는다 — 발전기 전환 전까지 냉방이 끊긴다.
        # 좋은 교보재다: 정전은 전력 문제로 시작해 열 문제로 끝난다.
        cooling_powered = self.power.utility_ok or self.power.generator_running

        for a in self.aisles.values():
            a.it_kw = sum(self._rack_kw.get(r["id"], 0.0)
                          for r in self.assets["racks"] if r["aisle"] == a.aisle)
            cap = 0.0
            for cid in a.crac_ids:
                if self._faulted("crac_fail", cid) or chiller_down or not cooling_powered:
                    continue
                crac = next(c for c in self.assets["facility"]["crac"] if c["id"] == cid)
                # 비례 제어 — 목표에 가까울수록 출력을 낮춘다
                duty = max(0.0, min((a.temp_c - self.target) / 4.0, 1.0))
                cap += float(crac["capacity_kw"]) * duty
            a.cooling_kw = cap

            leak = (self.ambient - a.temp_c) * 0.08
            a.temp_c += (a.it_kw - a.cooling_kw + leak) * dt_h / self.thermal_mass
            a.temp_c = max(10.0, min(a.temp_c, 80.0))

            a.humidity_pct = 45.0 - (a.temp_c - self.target) * 1.6
            if self._faulted("humidity_drift", a.aisle):
                a.humidity_pct = 78.0
            a.humidity_pct = max(5.0, min(a.humidity_pct, 95.0))

    # 경보 ------------------------------------------------------------
    def _metrics_for_eval(self):
        out = []
        for a in self.aisles.values():
            out.append((f"{a.floor}/aisle-{a.aisle}", {
                "temp_c": a.temp_c,
                "humidity_pct": a.humidity_pct,
                "crac_down": float(any(self._faulted("crac_fail", c) for c in a.crac_ids)),
                "smoke": float(self._faulted("smoke", a.floor)),
            }))
        out.append(("facility/power", {
            "ups_on_battery": float(self.power.on_battery),
            "ups_runtime_min": self.ups_runtime_min if self.power.on_battery else 999.0,
            "generator_failed": float(self.power.generator_failed),
            "chiller_down": float(any(self._faulted("chiller_fail", c["id"])
                                      for c in self.assets["facility"]["chiller"])),
        }))
        for pid, load in self.power.pdu_load.items():
            pdu = next(p for p in self.assets["facility"]["pdu"] if p["id"] == pid)
            out.append((f"facility/{pid}",
                        {"pdu_load_pct": load / float(pdu["capacity_kw"]) * 100.0}))
        for d in self.assets["facility"]["security"]:
            out.append((f"security/{d['id']}", {
                "door_forced": float(self._faulted("door_forced", d["id"])),
                "door_held": float(self._faulted("door_held", d["id"])),
                "cctv_offline": float(self._faulted("cctv_offline", d["id"])),
            }))
        return out

    @staticmethod
    def _cmp(v: float, op: str, thr: float) -> bool:
        return {">": v > thr, "<": v < thr, "==": abs(v - thr) < 1e-9}[op]

    def _evaluate_alarms(self):
        seen = set()
        for scope, metrics in self._metrics_for_eval():
            for rule in self.assets["alarms"]:
                m = rule["metric"]
                if m not in metrics:
                    continue
                key = f"{rule['id']}@{scope}"
                if self._cmp(metrics[m], rule["op"], float(rule["value"])):
                    seen.add(key)
                    if key not in self.active_alarms:
                        self.active_alarms[key] = {
                            "id": rule["id"], "scope": scope, "level": rule["level"],
                            "msg": rule["msg"], "metric": m,
                            "value": round(metrics[m], 2), "since": time.time()}
                        self._event("alarm", f"{rule['msg']} [{scope}] {m}={metrics[m]:.1f}",
                                    alarm_id=rule["id"], scope=scope, level=rule["level"])
                    else:
                        self.active_alarms[key]["value"] = round(metrics[m], 2)
        for key in list(self.active_alarms):
            if key not in seen:
                rec = self.active_alarms.pop(key)
                self._event("clear", f"경보 해제: {rec['msg']} [{rec['scope']}]",
                            alarm_id=rec["id"], scope=rec["scope"], level=3)

    def _event(self, kind: str, msg: str, **extra):
        self.events.append({"ts": time.time(), "kind": kind, "msg": msg, **extra})
        del self.events[:-500]

    # ── 조회 ───────────────────────────────────────────────────────
    def shed_analysis(self) -> list[dict]:
        """부하 그룹별 소비와 '차단하면' 영향 — ENV-03 판단 패널의 데이터."""
        groups = self.assets.get("shed_groups", {})
        rows = []
        for gid, g in groups.items():
            members = [a for a in self.assets["it_assets"] if a.get("shed_group") == gid]
            kw = sum(kw for aid, kw in self.asset_kw.items() if self._group_of(aid) == gid)
            rows.append({
                "group": gid, "name": g["name"], "kw": round(kw, 2),
                # 배치된 자산 수. 0 이면 "끊어도 아무 일이 없다" — 화면이 그걸 숨기면 안 된다.
                "assets": len(members),
                "priority": g.get("priority", 0),
                "impact": g.get("impact", ""), "recovery": g.get("recovery", ""),
                "shed": gid in self.shed,
                "runtime_if_shed_min": round(self.runtime_if_shed(gid), 1),
            })
        return sorted(rows, key=lambda r: -r["priority"])

    def state(self) -> dict:
        floors = {}
        for f in self.assets["floors"]:
            fid = f["id"]
            fa = [a for a in self.aisles.values() if a.floor == fid]
            floors[fid] = {
                "id": fid, "name": f["name"], "zone": f["zone"], "role": f["role"],
                "temp_c": round(sum(a.temp_c for a in fa) / len(fa), 1) if fa else None,
                "humidity_pct": round(sum(a.humidity_pct for a in fa) / len(fa), 1) if fa else None,
                "it_kw": round(sum(a.it_kw for a in fa), 2),
                "cooling_kw": round(sum(a.cooling_kw for a in fa), 2),
            }
        p = self.power
        return {
            "ts": time.time(),
            "building": self.assets["building"]["name"],
            "time_scale": self.time_scale,
            "floors": floors,
            "aisles": {a.aisle: {"aisle": a.aisle, "floor": a.floor,
                                 "temp_c": round(a.temp_c, 1),
                                 "humidity_pct": round(a.humidity_pct, 1),
                                 "it_kw": round(a.it_kw, 2),
                                 "cooling_kw": round(a.cooling_kw, 2),
                                 "crac": a.crac_ids} for a in self.aisles.values()},
            "power": {
                "utility_ok": p.utility_ok,
                "on_battery": p.on_battery,
                "generator_running": p.generator_running,
                "generator_failed": p.generator_failed,
                "ups_charge_pct": round(p.ups_charge_kwh / p.ups_capacity_kwh * 100, 1),
                "ups_runtime_min": round(self.ups_runtime_min, 1),
                "drain_pct_per_min": round(p.drain_pct_per_min, 2),
                "total_kw": round(p.total_kw, 2),
                "measured_kw": round(p.measured_kw, 3),   # 환산 전 실측 — 숨기지 않는다
                "rated_kw": float(self.assets["facility"]["ups"][0]["capacity_kw"]),
                "pdu": {k: round(v, 2) for k, v in p.pdu_load.items()},
            },
            "shed_analysis": self.shed_analysis(),
            "assets": {aid: {"kw": round(kw, 2), "util": round(self.asset_util.get(aid, 0.0), 3),
                             "shed_group": self._group_of(aid)}
                       for aid, kw in self.asset_kw.items()},
            "alarms": sorted(self.active_alarms.values(), key=lambda r: -r["level"]),
            "faults": {k: sorted(v) for k, v in self.faults.items() if v},
            "shed": sorted(self.shed),
        }
