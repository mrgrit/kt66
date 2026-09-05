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
    # ── 상류 전력 (교재 5.2.1) ─────────────────────────────────────
    # UPS 위쪽이다. 여기가 끊기면 UPS 는 배터리를 쓰기 시작하고 발전기가 받아야 한다.
    "substation_fail": "수전 변전소 정지 — 계통 전원 상실",
    "transformer_fail": "주변압기 과열·정지",
    "ats_fail": "자동절체스위치 절체 실패 — 발전기를 못 받는다",
    "busway_trip": "부스바 탭오프 차단",
    "fuel_leak": "연료탱크 누유 — 발전 지속시간 급감",
    "battery_runaway": "배터리 열폭주 — 리튬 화재",
    # ── 냉각 계통 (교재 5.3) — 바깥 루프일수록 파급이 크다 ─────────
    "cooling_tower_fail": "냉각탑 정지 — 응축수 방열 중단",
    "pump_fail": "냉각수 펌프 정지 — 순환 중단",
    "hx_fouling": "열교환기 오염 — 전열 성능 저하",
    "economizer_stuck": "이코노마이저 댐퍼 고착 — 자연냉각 불가",
    "cdu_leak": "냉각분배장치 누수",
    "fan_coil_fail": "인로우 팬코일 정지",
    "containment_open": "핫아일 격리 개방 — 열기 혼합",
    "airflow_block": "타공타일 막힘 — 기류 부족",
    "heatwave": "외기 습구온도 급상승 — 냉각탑 능력 저하",
}

# 냉각은 **직렬 체인**이다. 한 곳이 끊기면 하류 전체가 0 이 된다.
# 이 상수는 그 체인의 순서를 이름으로 박아 둔 것이다 — 관제 화면이 그대로 그린다.
COOLING_CHAIN = ["cooling_tower", "condenser_pump", "heat_exchanger",
                 "chiller_or_economizer", "process_pump", "air_handler"]


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
    fcu_ids: list[str] = field(default_factory=list)   # 인로우 팬코일 (교재 5.3.7)
    containment: str = "none"        # hot-aisle | cold-aisle | none
    airflow_pct: float = 100.0       # 요구 대비 공급 기류. 100 미만이면 재순환이 생긴다


@dataclass
class PlantState:
    """냉각 플랜트 — 아일 바깥의 모든 것. 교재 5.3.1 의 3루프를 그대로 담는다.

    capacity_kw 는 **체인의 최솟값**이다. 냉각탑이 40kW 여도 펌프가 죽으면 0 이다.
    bottleneck 은 그 최솟값을 만든 고리의 이름이고, 관제 화면이 이것을 그대로 띄운다 —
    "왜 3F 가 뜨거운가"의 답이 한 단어로 나와야 하기 때문이다.
    """
    capacity_kw: float = 0.0
    bottleneck: str = "none"
    tower_kw: float = 0.0
    chiller_kw: float = 0.0           # 냉동기가 실제로 걷어낸 열
    economizer_kw: float = 0.0        # 자연 냉각이 걷어낸 열
    free_cooling: bool = False        # 냉동기를 껐는가 — PUE 를 가르는 스위치
    chiller_running: bool = False
    pumps_ok: dict[str, bool] = field(default_factory=dict)
    hx_fouling_pct: float = 0.0
    buffer_kwh: float = 0.0           # 물탱크 축열 잔량. 냉각 상실 시 몇 분을 벌어 준다
    reject_kw: float = 0.0            # 실제 방열량
    coldplate_inlet_c: float = 18.0


@dataclass
class EfficiencyState:
    """교재 9장의 지표. 경보가 아니라 **운영 품질**이다.

    PUE 는 총 시설 전력 / IT 전력. 1.0 이 하한이다. 자연 냉각이 걸리면 뚝 떨어지고
    냉동기가 켜지면 뛴다 — 학생이 습구 온도 하나로 PUE 가 움직이는 것을 보게 된다.
    """
    it_kw: float = 0.0
    facility_kw: float = 0.0          # 냉각·전력손실 등 오버헤드
    total_kw: float = 0.0
    pue: float = 1.0
    wue_l_per_kwh: float = 0.0
    cue_kg_per_kwh: float = 0.0
    water_lpm: float = 0.0
    critical_power_pct: float = 0.0
    breakdown: dict[str, float] = field(default_factory=dict)


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
        for fcu in assets["facility"].get("fan_coil", []):
            if fcu.get("aisle") in self.aisles:
                self.aisles[fcu["aisle"]].fcu_ids.append(fcu["id"])
        for cont in assets["facility"].get("containment", []):
            if cont.get("aisle") in self.aisles:
                self.aisles[cont["aisle"]].containment = cont.get("kind", "none")

        ups = assets["facility"]["ups"][0]
        cap = float(ups["battery_kwh"])
        self.power = PowerState(ups_capacity_kwh=cap, ups_charge_kwh=cap)
        self.asset_kw: dict[str, float] = {}
        self.asset_util: dict[str, float] = {}
        self._rack_kw: dict[str, float] = {}

        # ── 신규 계통 상태 ──────────────────────────────────────────
        self.plant = PlantState()
        self.eff = EfficiencyState()
        fac = assets["facility"]
        tank = (fac.get("water_tank") or [{}])[0]
        self.plant.buffer_kwh = float(tank.get("thermal_buffer_kwh", 0.0))
        self._buffer_max = self.plant.buffer_kwh
        # 배터리 온도는 개별로 추적한다 — 열폭주는 스트링 하나에서 시작한다
        self.batt_temp: dict[str, float] = {
            b["id"]: float(b.get("temp_c", 25.0)) for b in fac.get("battery", [])}
        self.batt_runaway: set[str] = set()
        # 연료는 발전기가 돌 때만 준다. 8시간이 지나면 발전기도 멈춘다
        ft = (fac.get("fuel_tank") or [{}])[0]
        self.fuel_liters = float(ft.get("liters", 0.0))
        self._fuel_max = self.fuel_liters
        # 외기 — 습구 온도가 냉각탑 능력을 정한다
        wx = fac.get("weather", {})
        self.wx_drybulb = float(wx.get("drybulb_c", 18.0))
        self.wx_wetbulb = float(wx.get("wetbulb_c", 13.0))
        self._wx_wetbulb_base = self.wx_wetbulb
        self.water_used_l = 0.0

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
        self._compute_battery(dt_h)
        self._compute_cooling_plant(dt_h)   # 아일보다 먼저. 플랜트가 아일의 상한이다
        self._compute_thermal(dt_h)
        self._compute_efficiency(dt_h)
        self._evaluate_alarms()

    # ── 냉각 플랜트 (교재 5.3.1 의 3루프) ─────────────────────────────
    def _compute_cooling_plant(self, dt_h: float):
        """체인의 최솟값이 곧 플랜트 능력이다.

        냉각탑 ─▶ 응축수펌프 ─▶ 열교환기 ─▶ (냉동기 | 이코노마이저) ─▶ 프로세스수펌프

        직렬이므로 어느 한 고리가 0 이면 전체가 0 이다. 이 성질 때문에 "냉각탑 한 대가
        3F GPU 를 멈춘다"가 시나리오 장치 없이 물리에서 그냥 나온다.
        """
        fac = self.assets["facility"]
        p = self.plant
        powered = self.power.utility_ok or self.power.generator_running

        # 외기부터. 습구 온도가 이 아래 모든 계산의 입력이다(교재 5.3.4·5.3.5).
        wx = fac.get("weather", {})
        self.wx_wetbulb = self._wx_wetbulb_base
        self.wx_drybulb = float(wx.get("drybulb_c", 18.0))
        if self._faulted("heatwave", wx.get("id", "wx-01")):
            self.wx_wetbulb = self._wx_wetbulb_base + 14.0   # 폭염 — 냉각탑이 무력해진다
            self.wx_drybulb += 14.0

        # ① 냉각탑 — 도달 가능한 물 온도는 습구 + approach 다(교재 5.3.5).
        #    습구가 오르면 능력이 떨어진다. 설계점 습구 13°C 를 기준으로 선형 감쇠.
        tower_kw = 0.0
        freeze = False
        for ct in fac.get("cooling_tower", []):
            if self._faulted("cooling_tower_fail", ct["id"]) or not powered:
                continue
            if self.wx_drybulb <= float(ct.get("freeze_risk_c", -99)):
                freeze = True           # 결빙 위험. 능력은 유지하되 경보를 띄운다
            derate = max(0.25, min(1.0, 1.0 - (self.wx_wetbulb - 13.0) * 0.055))
            tower_kw += float(ct["capacity_kw"]) * derate
        p.tower_kw = tower_kw
        self._tower_freeze = freeze

        # ② 펌프 — 물이 안 돌면 아래는 전부 무의미하다
        pumps_ok, cond_ok, proc_ok = {}, False, False
        for pu in fac.get("pump", []):
            ok = not self._faulted("pump_fail", pu["id"]) and powered
            pumps_ok[pu["id"]] = ok
            if ok and pu.get("loop") == "condenser":
                cond_ok = True
            if ok and pu.get("loop") == "process":
                proc_ok = True
        p.pumps_ok = pumps_ok

        # ③ 열교환기 — 오염되면 전열이 떨어진다(유지보수 실습의 대상)
        hx = (fac.get("heat_exchanger") or [{}])[0]
        fouling = float(hx.get("fouling_pct", 0.0))
        if self._faulted("hx_fouling", hx.get("id", "hx-01")):
            fouling = 45.0
        p.hx_fouling_pct = fouling
        hx_kw = float(hx.get("capacity_kw", 999.0)) * (1.0 - fouling / 100.0)

        # ④ 냉동기 vs 이코노마이저 — 습구가 낮으면 냉동기를 끈다(교재 5.3.4)
        eco = (fac.get("economizer") or [{}])[0]
        eco_ok = (bool(eco) and eco.get("damper_ok", True)
                  and not self._faulted("economizer_stuck", eco.get("id", "eco-01"))
                  and powered)
        free = eco_ok and self.wx_wetbulb <= float(eco.get("enable_wetbulb_c", -99))

        chiller_kw = 0.0
        for c in fac.get("chiller", []):
            if not self._faulted("chiller_fail", c["id"]) and powered:
                chiller_kw += float(c["capacity_kw"])
        eco_kw = float(eco.get("capacity_kw", 0.0)) if eco_ok else 0.0

        # 자연 냉각이 걸리면 냉동기는 대기한다. 부족하면 냉동기가 보조로 켜진다.
        source_kw = eco_kw if free else chiller_kw
        if free and eco_kw < self._demand_kw():
            source_kw = eco_kw + chiller_kw     # 하이브리드 — 모자란 만큼만 냉동기
            free = False
        p.free_cooling = free
        p.chiller_running = (not free) and chiller_kw > 0

        # ── 체인의 최솟값을 찾는다. 이름을 같이 들고 다닌다 ──────────
        links = [
            ("cooling_tower", tower_kw),
            ("condenser_pump", 999.0 if cond_ok else 0.0),
            ("heat_exchanger", hx_kw),
            ("economizer" if free else "chiller", source_kw),
            ("process_pump", 999.0 if proc_ok else 0.0),
        ]
        p.bottleneck, p.capacity_kw = min(links, key=lambda kv: kv[1])
        if p.capacity_kw >= 999.0:
            p.capacity_kw = min(tower_kw, hx_kw, source_kw)
            p.bottleneck = "none"

        # ⑤ 물탱크 축열 — 냉각이 끊겨도 몇 분을 벌어 준다(교재 5.3.1)
        demand = self._demand_kw()
        if p.capacity_kw < demand and self._buffer_max > 0:
            draw = min((demand - p.capacity_kw) * dt_h, p.buffer_kwh)
            p.buffer_kwh -= draw
            if draw > 0:
                p.capacity_kw += draw / dt_h if dt_h > 0 else 0.0
        else:
            p.buffer_kwh = min(self._buffer_max, p.buffer_kwh + demand * dt_h * 0.3)

        # 실제로 걷어낸 열이지 **용량이 아니다**. 냉동기 전력은 걷어낸 열에 비례하므로
        # 여기서 용량을 쓰면 유휴 상태에서도 PUE 가 치솟는다.
        p.reject_kw = max(0.0, min(p.capacity_kw, demand))
        p.chiller_kw = min(chiller_kw, p.reject_kw) if p.chiller_running else 0.0
        p.economizer_kw = min(eco_kw, p.reject_kw) if free else 0.0

        # ⑥ 콜드플레이트 입수 온도 — 프로세스수 온도를 따라간다(교재 5.3.7)
        cp = (fac.get("cold_plate") or [{}])[0]
        base = float(cp.get("inlet_c", 18.0))
        shortfall = max(0.0, demand - p.capacity_kw)
        p.coldplate_inlet_c = base + shortfall * 0.6 + max(0.0, self.wx_wetbulb - 13.0) * 0.4
        if self._faulted("cdu_leak", (fac.get("cdu") or [{}])[0].get("id", "cdu-01")):
            p.coldplate_inlet_c += 8.0     # 유량 상실 — 입수가 올라간다

    def _demand_kw(self) -> float:
        """지금 걷어내야 하는 열. IT 소비 전력이 곧 발열이다."""
        return sum(self._rack_kw.values())

    # ── 배터리 (교재 5.2.3 — 리튬 열폭주) ────────────────────────────
    def _compute_battery(self, dt_h: float):
        """방전하면 더워진다. 60°C 를 넘으면 열폭주 — 카카오 화재가 이것이다.

        주변 온도(1F)를 따라가되, 방전 중에는 자체 발열이 더해진다.
        """
        fac = self.assets["facility"]
        discharging = self.power.on_battery
        for b in fac.get("battery", []):
            bid = b["id"]
            t = self.batt_temp.get(bid, 25.0)
            if self._faulted("battery_runaway", bid):
                t += 40.0 * dt_h * 60          # 주입 시 급상승
            elif discharging:
                # C-rate 에 비례한 자체 발열. 부하가 클수록 빨리 더워진다.
                # 계수는 **배터리가 바닥날 무렵 열폭주 문턱에 닿도록** 잡았다 —
                # "전원이 곧 복구되겠지" 하고 기다리는 선택이 화재로 이어지게 하려는 것이다.
                crate = self.power.total_kw / max(float(b.get("kwh", 6.0)), 0.1)
                t += crate * 0.35 * dt_h * 60
            else:
                t += (25.0 - t) * min(1.0, dt_h * 30)   # 식는다
            t = max(10.0, min(t, 200.0))
            self.batt_temp[bid] = t
            if t >= float(b.get("runaway_temp_c", 60.0)):
                if bid not in self.batt_runaway:
                    self.batt_runaway.add(bid)
                    self._event("alarm",
                                f"배터리 열폭주 개시 — {b.get('name', bid)} {t:.0f}°C. "
                                f"소화설비·격리 즉시", battery=bid, level=15)
            elif bid in self.batt_runaway and t < 50.0:
                self.batt_runaway.discard(bid)

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
            # 실측 원값 — 환산 전. measured_* 를 선언한 자산만 집계한다. 지금은
            # dgx-spark-01 **하나뿐**이다(90~240W). 나머지는 컨테이너라 잴 것이 없다.
            # 그래서 이 값은 "데이터센터 전체 실측"이 아니라 **실물 한 대의 실측**이고,
            # total_kw(모델값 9kW대)와 두 자릿수 배로 벌어지는 것이 정상이다. 그 간극이
            # 바로 이 랩의 환산비다 — 숨기지 않는 것이 목적이라 지우지 않는다.
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
        # 상용 전원은 수전·변전소·변압기가 **모두** 살아 있어야 온다(교재 5.2.1 의 직렬 경로).
        # 학생이 "정전"이라고 뭉뚱그리던 것을 계통도의 한 지점으로 좁히게 만드는 장치다.
        fac = self.assets["facility"]
        p.utility_ok = (not self._faulted("utility_fail", "utility-01")
                        and not any(self._faulted("substation_fail", s["id"])
                                    for s in fac.get("substation", []))
                        and not any(self._faulted("transformer_fail", t["id"])
                                    for t in fac.get("transformer", [])))
        gen = fac["generator"][0]

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
            # ATS 가 절체를 못 하면 발전기가 돌아도 부하를 못 받는다. 발전기 자체는
            # 멀쩡한데 전기가 안 온다 — 실무에서 가장 흔한 오진 지점이라 따로 뒀다.
            ats_blocked = any(self._faulted("ats_fail", a["id"]) for a in fac.get("ats", []))
            if elapsed_s >= float(gen["start_delay_s"]):
                why = ("ATS 절체 실패" if ats_blocked else
                       "연료 고갈" if self.fuel_liters <= 0 else
                       "기동 실패" if self._faulted("generator_fail", gen["id"]) else "")
                if why:
                    # **이미 돌고 있었더라도 여기서 부하를 놓는다.** 예전에는 기동에
                    # 실패하는 경우만 봤다: generator_running 이 이미 True 면
                    # generator_failed 만 세우고 부하는 그대로 뒀다. 그래서 수전이 끊겨
                    # 발전기가 받은 뒤에 발전기를 고장내면, 고장난 발전기가 부하를 계속
                    # 먹고 UPS 는 충전까지 하는 상태가 됐다 — 화면에는 generator_running
                    # 과 generator_failed 가 **동시에 참**으로 찍혔고, 배터리는 줄지
                    # 않았다. ENV-03(수전 상실 + 발전기 실패)이 전개되지 않던 원인이다.
                    if p.generator_running:
                        p.generator_running = False
                        self._event("alarm",
                                    f"비상 발전기 부하 상실 ({why}) — 배터리로 되돌아간다 ({gen['id']})")
                    if not p.generator_failed:
                        p.generator_failed = True
                        self._event("alarm",
                                    f"비상 발전기 부하 인수 불가 ({why}) — 배터리만 남았다 ({gen['id']})")
                else:
                    # 고장을 해제하면 정전 중이라도 다시 받는다. 예전에는 generator_failed
                    # 가 수전 복구 때까지 안 풀려서, 강사가 고장을 걷어도 GEN_FAIL 경보가
                    # 남았다 — 복구 절차를 가르칠 수가 없었다.
                    if p.generator_failed:
                        p.generator_failed = False
                        self._event("info", f"비상 발전기 고장 해제 ({gen['id']})")
                    if not p.generator_running:
                        p.generator_running = True
                        self._event("info", f"비상 발전기 기동 — 부하 인수 ({gen['id']})")

            if p.generator_running:
                p.on_battery = False
                p.ups_charge_kwh = min(p.ups_capacity_kwh,
                                       p.ups_charge_kwh + p.ups_capacity_kwh * 0.03 * dt_h * 60)
            else:
                p.on_battery = True
                p.ups_charge_kwh = max(0.0, p.ups_charge_kwh - p.total_kw * dt_h)

        # 연료 소비 — 발전기가 도는 동안만. 누유가 있으면 훨씬 빨리 준다.
        ft = (fac.get("fuel_tank") or [{}])[0]
        if p.generator_running and ft:
            gen_cap = float(gen["capacity_kw"]) or 1.0
            rate = float(ft.get("lph_at_full_load", 480.0)) * max(0.15, p.total_kw / gen_cap)
            if self._faulted("fuel_leak", ft.get("id", "tank-fuel-01")):
                rate *= 4.0
            self.fuel_liters = max(0.0, self.fuel_liters - rate * dt_h)
        elif ft and not p.generator_running:
            self.fuel_liters = min(self._fuel_max, self.fuel_liters)

        # 분당 감소율 — 캡처의 "분당 5.4% 감소"
        if p.on_battery and p.ups_capacity_kwh > 0:
            p.drain_pct_per_min = p.total_kw / p.ups_capacity_kwh * 100.0 / 60.0
        else:
            p.drain_pct_per_min = 0.0

    @property
    def measured_scope(self) -> list[str]:
        """measured_kw 가 실제로 덮는 자산. 화면이 '전체 실측'으로 읽지 않게 한다."""
        return [a["id"] for a in self.assets["it_assets"] if "measured_idle_w" in a]

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
        # 실제 DC 에서 CRAC 은 보통 UPS 를 타지 않는다 — 발전기 전환 전까지 냉방이 끊긴다.
        # 좋은 교보재다: 정전은 전력 문제로 시작해 열 문제로 끝난다.
        cooling_powered = self.power.utility_ok or self.power.generator_running
        fac = self.assets["facility"]

        # 이중마루 기류 — 공급이 요구에 못 미치면 랙 상단에서 재순환이 생긴다(교재 5.3.6).
        # 그러면 공조기가 아무리 돌아도 서버가 마시는 공기는 더 뜨겁다.
        airflow = {}
        for rf in fac.get("raised_floor", []):
            req = float(rf.get("required_cfm", 0)) or 1.0
            sup = float(rf.get("supplied_cfm", req))
            if self._faulted("airflow_block", rf["id"]):
                sup *= 0.6
            airflow[rf["floor"]] = min(100.0, sup / req * 100.0)

        # 아일별 공조 요구를 먼저 모아 플랜트 능력을 비례 배분한다.
        # 플랜트가 모자라면 모든 아일이 같은 비율로 굶는다 — 실제 배관이 그렇게 동작한다.
        want: dict[str, float] = {}
        for a in self.aisles.values():
            a.it_kw = sum(self._rack_kw.get(r["id"], 0.0)
                          for r in self.assets["racks"] if r["aisle"] == a.aisle)
            a.airflow_pct = airflow.get(a.floor, 100.0)
            cap = 0.0
            for cid in a.crac_ids:
                if self._faulted("crac_fail", cid) or not cooling_powered:
                    continue
                crac = next(c for c in fac["crac"] if c["id"] == cid)
                cap += float(crac["capacity_kw"])
            for fid in a.fcu_ids:
                if self._faulted("fan_coil_fail", fid) or not cooling_powered:
                    continue
                fcu = next(f for f in fac["fan_coil"] if f["id"] == fid)
                cap += float(fcu["capacity_kw"])

            # 컨테인먼트 — 격리하면 같은 공조기로 더 많이 걷어낸다(교재 5.3.6).
            # 열어 두면 그 이득이 사라진다. 문 하나가 효율을 정한다.
            cont = next((c for c in fac.get("containment", [])
                         if c.get("aisle") == a.aisle), None)
            if cont and cont.get("sealed") and not self._faulted("containment_open", cont["id"]):
                cap *= 1.0 + float(cont.get("efficiency_gain", 0.0))
            # 기류가 모자라면 그 비율만큼만 실제로 걷어낸다
            cap *= a.airflow_pct / 100.0
            # 비례 제어 — 목표에 가까울수록 출력을 낮춘다
            duty = max(0.0, min((a.temp_c - self.target) / 4.0, 1.0))
            want[a.aisle] = cap * duty

        total_want = sum(want.values())
        avail = self.plant.capacity_kw
        ratio = 1.0 if total_want <= avail or total_want <= 0 else avail / total_want

        for a in self.aisles.values():
            a.cooling_kw = want[a.aisle] * ratio

            leak = (self.ambient - a.temp_c) * 0.08
            a.temp_c += (a.it_kw - a.cooling_kw + leak) * dt_h / self.thermal_mass
            a.temp_c = max(10.0, min(a.temp_c, 80.0))

            a.humidity_pct = 45.0 - (a.temp_c - self.target) * 1.6
            if self._faulted("humidity_drift", a.aisle):
                a.humidity_pct = 78.0
            a.humidity_pct = max(5.0, min(a.humidity_pct, 95.0))

    # 효율 (교재 9장) ---------------------------------------------------
    def _compute_efficiency(self, dt_h: float):
        """PUE·WUE·CUE. 경보가 아니라 운영 품질의 척도다.

        오버헤드를 항목별로 쪼개 둔다(breakdown). "PUE 가 1.7 이다" 보다
        "그중 0.4 가 냉동기다" 가 훨씬 쓸모 있는 정보이기 때문이다 — 교재 9.2.3 의
        에너지 비효율 분해가 바로 이것이다.
        """
        fac = self.assets["facility"]
        b = self.assets["building"]
        e = self.eff
        it_kw = sum(self._rack_kw.values())
        p = self.plant

        # 냉동기 — 걷어낸 열을 COP 로 나눈 것이 소비 전력이다
        cop = float((fac.get("chiller") or [{}])[0].get("cop", 5.0))
        chiller_kw = p.chiller_kw / cop if p.chiller_running else 0.0

        # 팬·펌프 — 돌고 있는 것만 센다. 부하율(load)에 따라 소비도 움직인다.
        # 팬은 목표 온도에 닿아도 완전히 멈추지 않는다 — 공기는 계속 돌아야 하므로
        # 30% 를 바닥으로 두고 나머지를 부하에 비례시킨다.
        cap_total = max(self.plant.capacity_kw, 0.01)
        load = max(0.3, min(1.0, p.reject_kw / cap_total))
        fan_kw = 0.0
        for c in fac.get("crac", []):
            if not self._faulted("crac_fail", c["id"]):
                fan_kw += float(c.get("fan_kw", 0.0)) * load
        for f in fac.get("fan_coil", []):
            if not self._faulted("fan_coil_fail", f["id"]):
                fan_kw += float(f.get("fan_kw", 0.0)) * load
        tower_kw = 0.0
        water_lpm = 0.0
        for ct in fac.get("cooling_tower", []):
            if self._faulted("cooling_tower_fail", ct["id"]):
                continue
            share = min(1.0, p.reject_kw / max(float(ct["capacity_kw"]), 0.01))
            tower_kw += float(ct.get("fan_kw", 0.0)) * max(0.3, share)
            # 증발 보충수 — 걷어낸 열에 비례한다. WUE 의 분자다
            water_lpm += float(ct.get("makeup_lpm", 0.0)) * share
        pump_kw = sum(float(pu.get("kw", 0.0)) for pu in fac.get("pump", [])
                      if p.pumps_ok.get(pu["id"]) and not pu.get("standby"))

        # 전력 변환 손실 — UPS 와 변압기. 교재 5.2.2·5.2.3
        ups_eff = float((fac.get("ups") or [{}])[0].get("efficiency", 0.96))
        tr_eff = float((fac.get("transformer") or [{}])[0].get("efficiency", 0.985))
        ups_loss = it_kw * (1.0 / ups_eff - 1.0)
        tr_loss = it_kw * (1.0 / tr_eff - 1.0)

        e.breakdown = {
            "chiller": round(chiller_kw, 2),
            "cooling_tower": round(tower_kw, 2),
            "pump": round(pump_kw, 2),
            "fan": round(fan_kw, 2),
            "ups_loss": round(ups_loss, 2),
            "transformer_loss": round(tr_loss, 2),
        }
        e.it_kw = it_kw
        e.facility_kw = sum(e.breakdown.values())
        e.total_kw = it_kw + e.facility_kw
        e.pue = round(e.total_kw / it_kw, 3) if it_kw > 0.01 else 1.0
        e.water_lpm = round(water_lpm, 2)
        self.water_used_l += water_lpm * dt_h * 60.0
        e.wue_l_per_kwh = round(water_lpm * 60.0 / it_kw, 2) if it_kw > 0.01 else 0.0
        e.cue_kg_per_kwh = round(
            float(b.get("grid_carbon_kg_per_kwh", 0.443)) * e.pue, 3)
        crit = float(b.get("critical_power_kw", 45.0)) or 1.0
        e.critical_power_pct = round(it_kw / crit * 100.0, 1)

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

        # ── 신규 계통 ────────────────────────────────────────────────
        fac = self.assets["facility"]
        p, e = self.plant, self.eff
        for s in fac.get("substation", []):
            out.append((f"facility/{s['id']}",
                        {"substation_down": float(self._faulted("substation_fail", s["id"]))}))
        for t in fac.get("transformer", []):
            out.append((f"facility/{t['id']}",
                        {"transformer_hot": float(self._faulted("transformer_fail", t["id"]))}))
        for a in fac.get("ats", []):
            out.append((f"facility/{a['id']}",
                        {"ats_failed": float(self._faulted("ats_fail", a["id"]))}))
        for bw in fac.get("busway", []):
            load = sum(self._rack_kw.get(r, 0.0) for r in bw.get("tap_offs", []))
            if self._faulted("busway_trip", bw["id"]):
                load = float(bw["capacity_kw"]) * 0.99
            out.append((f"facility/{bw['id']}",
                        {"busway_load_pct": load / float(bw["capacity_kw"]) * 100.0}))
        out.append(("facility/fuel", {"fuel_hours_left": self.fuel_hours_left}))
        for b in fac.get("battery", []):
            out.append((f"facility/{b['id']}", {
                "battery_temp_c": self.batt_temp.get(b["id"], 25.0),
                "battery_runaway": float(b["id"] in self.batt_runaway),
            }))
        for ct in fac.get("cooling_tower", []):
            out.append((f"facility/{ct['id']}", {
                "cooling_tower_down": float(self._faulted("cooling_tower_fail", ct["id"])),
                "cooling_tower_freeze": float(getattr(self, "_tower_freeze", False)),
            }))
        for pu in fac.get("pump", []):
            if pu.get("standby"):
                continue          # 대기 펌프는 정지가 정상이다 — 경보를 내면 안 된다
            out.append((f"facility/{pu['id']}",
                        {"pump_down": float(not p.pumps_ok.get(pu["id"], True))}))
        for hx in fac.get("heat_exchanger", []):
            out.append((f"facility/{hx['id']}", {"hx_fouling_pct": p.hx_fouling_pct}))
        for eco in fac.get("economizer", []):
            out.append((f"facility/{eco['id']}",
                        {"economizer_stuck": float(self._faulted("economizer_stuck", eco["id"]))}))
        for cd in fac.get("cdu", []):
            out.append((f"facility/{cd['id']}",
                        {"cdu_leak": float(self._faulted("cdu_leak", cd["id"]))}))
        for cp in fac.get("cold_plate", []):
            out.append((f"facility/{cp['id']}", {"coldplate_inlet_c": p.coldplate_inlet_c}))
        for cont in fac.get("containment", []):
            if cont.get("sealed"):
                out.append((f"facility/{cont['id']}",
                            {"containment_open": float(self._faulted("containment_open", cont["id"]))}))
        for a in self.aisles.values():
            out.append((f"{a.floor}/aisle-{a.aisle}/air",
                        {"airflow_deficit_pct": max(0.0, 100.0 - a.airflow_pct)}))
        out.append(("facility/weather", {"wetbulb_c": self.wx_wetbulb}))
        # PUE 는 **부하가 있을 때만** 경보로 의미가 있다. 펌프·팬 같은 고정 기생전력은
        # IT 부하가 낮을수록 비율로 커지므로, 유휴 상태의 높은 PUE 는 고장이 아니라
        # 에너지 비례성의 당연한 결과다(교재 9.3.2). 그걸 경보로 띄우면 화면이 늘
        # 빨갛고, 학생은 곧 경보를 무시하는 법부터 배운다. 그래서 임계전력 25% 미만에서는
        # 지표로만 보여 주고 경보 평가에서는 뺀다 — 숫자는 /state 에 그대로 있다.
        if e.critical_power_pct >= 25.0:
            out.append(("facility/efficiency", {"pue": e.pue}))
        out.append(("facility/capacity", {"critical_power_pct": e.critical_power_pct}))
        return out

    @property
    def fuel_hours_left(self) -> float:
        """연료 잔량 ÷ 소비율. 발전기가 안 돌면 무한대로 본다."""
        ft = (self.assets["facility"].get("fuel_tank") or [{}])[0]
        lph = float(ft.get("lph_at_full_load", 480.0))
        if not self.power.generator_running or lph <= 0:
            return 999.0
        gen_cap = float(self.assets["facility"]["generator"][0]["capacity_kw"]) or 1.0
        rate = lph * max(0.15, self.power.total_kw / gen_cap)
        return self.fuel_liters / rate

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
                "measured_kw": round(p.measured_kw, 3),   # 실물(dgx-spark-01) 실측. 전체 아님
                "measured_scope": self.measured_scope,
                "rated_kw": float(self.assets["facility"]["ups"][0]["capacity_kw"]),
                "pdu": {k: round(v, 2) for k, v in p.pdu_load.items()},
            },
            # ── 냉각 플랜트 — 아일 바깥의 3루프 (교재 5.3.1) ─────────
            # bottleneck 이 이 블록의 핵심이다. "무엇이 막고 있는가"를 한 단어로 준다.
            "plant": {
                "capacity_kw": round(self.plant.capacity_kw, 2),
                "demand_kw": round(self._demand_kw(), 2),
                "bottleneck": self.plant.bottleneck,
                "tower_kw": round(self.plant.tower_kw, 2),
                "chiller_kw": round(self.plant.chiller_kw, 2),
                "economizer_kw": round(self.plant.economizer_kw, 2),
                "free_cooling": self.plant.free_cooling,
                "chiller_running": self.plant.chiller_running,
                "hx_fouling_pct": round(self.plant.hx_fouling_pct, 1),
                "buffer_kwh": round(self.plant.buffer_kwh, 2),
                "coldplate_inlet_c": round(self.plant.coldplate_inlet_c, 1),
                "pumps": self.plant.pumps_ok,
                "chain": COOLING_CHAIN,
            },
            # ── 외기 — 자연 냉각의 가부를 정한다 ─────────────────────
            "weather": {
                "drybulb_c": round(self.wx_drybulb, 1),
                "wetbulb_c": round(self.wx_wetbulb, 1),
                "free_cooling_available": self.plant.free_cooling,
            },
            # ── 효율 (교재 9장) ──────────────────────────────────────
            "efficiency": {
                "pue": self.eff.pue,
                "wue_l_per_kwh": self.eff.wue_l_per_kwh,
                "cue_kg_per_kwh": self.eff.cue_kg_per_kwh,
                "it_kw": round(self.eff.it_kw, 2),
                "facility_kw": round(self.eff.facility_kw, 2),
                "total_kw": round(self.eff.total_kw, 2),
                "water_lpm": self.eff.water_lpm,
                "water_used_l": round(self.water_used_l, 1),
                "critical_power_pct": self.eff.critical_power_pct,
                "critical_power_kw": float(self.assets["building"].get("critical_power_kw", 45.0)),
                "breakdown": self.eff.breakdown,
            },
            # ── 배터리·연료 — 정전 대응의 두 시계 ────────────────────
            "battery": {bid: {"temp_c": round(t, 1), "runaway": bid in self.batt_runaway}
                        for bid, t in self.batt_temp.items()},
            "fuel": {"liters": round(self.fuel_liters, 1),
                     "hours_left": round(self.fuel_hours_left, 2)},
            "shed_analysis": self.shed_analysis(),
            "assets": {aid: {"kw": round(kw, 2), "util": round(self.asset_util.get(aid, 0.0), 3),
                             "shed_group": self._group_of(aid)}
                       for aid, kw in self.asset_kw.items()},
            "alarms": sorted(self.active_alarms.values(), key=lambda r: -r["level"]),
            "faults": {k: sorted(v) for k, v in self.faults.items() if v},
            "shed": sorted(self.shed),
        }
