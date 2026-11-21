import time
from app.config import ROOT, MODULES
from app.errors import DomainError
from app.values import scalar
from app.manifest import digest, manifest
from app import rule_registry, store
from app.modules.m1_taps.rules import evaluate as stage1
from app.modules.m2_routes.rules import evaluate as stage2
from app.modules.m3_fares.rules import evaluate as stage3
from app.modules.m4_journeys.rules import evaluate as stage4
from app.modules.m5_discounts.rules import evaluate as stage5
from app.modules.m6_clearing.rules import evaluate as stage6
from app.modules.m7_settlement.rules import evaluate as stage7

STAGES = [stage1, stage2, stage3, stage4, stage5, stage6, stage7]


def load_sample(name):
    if name not in {"dirty.csv", "clean.csv", "legacy.csv", "month_legacy.csv", "month_corrected.csv"}:
        raise DomainError("BAD_SAMPLE", "样例不存在", 404)
    return (ROOT / "sample_data" / name).read_bytes()


def _resolve_rule_version(raw, params):
    """决定本次计算使用的规则版本：显式 pin > 乘车时刻 as-of > 当前规则。"""
    mode = params.get("rule_mode", "current")
    pin = params.get("rule_version")
    if pin:
        rule_registry.get(pin)
        return pin, "pinned"
    if mode == "as_of":
        # 以首批可解析刷卡时间所在业务时刻为准（用 m1 行的 event_time）。
        preview = stage1(raw, {"parameters": {}, "quarantine": []})
        times = [r.get("event_time") for r in preview["rows"] if r.get("event_time")]
        if not times:
            raise DomainError("NO_USABLE_TIME", "没有可用于判定生效规则的刷卡时间")
        return rule_registry.as_of(min(times)), "as_of"
    return rule_registry.current(), "current"


def _ruleset_params(rule_version, caller_params):
    """登记规则集为默认值，调用方显式参数仍可覆盖（保留旧的临时试算能力）。"""
    frozen = dict(rule_registry.rules_of(rule_version))
    fares = caller_params.get("fares") or frozen["fares"]
    merged = {
        "fares": fares,
        "daily_cap": caller_params.get("daily_cap", frozen["daily_cap"]),
        "transfer_discount": caller_params.get("transfer_discount", frozen["transfer_discount"]),
        "transfer_minutes": caller_params.get("transfer_minutes", frozen["transfer_minutes"]),
    }
    return {**caller_params, **merged}


def calculate(raw, params=None, stop=6):
    params = params or {}
    rule_version, rule_mode = _resolve_rule_version(raw, params)
    effective_params = _ruleset_params(rule_version, params)
    started = time.monotonic()
    context = {"parameters": effective_params, "raw": raw, "quarantine": []}
    stages = []
    rows = []
    for index, stage in enumerate(STAGES[: stop + 1]):
        result = stage(raw if index == 0 else rows, context)
        result = scalar(result)
        rows = result["rows"]
        context["quarantine"].extend(result.get("quarantine", []))
        if index == 0:
            context["intake"] = result["metrics"]
        result["cumulative_quarantine"] = list(context["quarantine"])
        context[MODULES[index][0]] = result
        stages.append(result)
    return {
        "run_id": digest(raw, effective_params, rule_version)[:20],
        "stages": stages,
        "manifest": manifest(raw, effective_params, started, rule_version, rule_mode),
        "parameters": effective_params,
    }


def execute(raw, params=None, batch_ref=None, kind="复算", change_note=""):
    result = calculate(raw, params)
    return store.persist(result, batch_ref=batch_ref, kind=kind, change_note=change_note)


def recalc(batch_ref, raw, params=None, kind="复算", change_note=""):
    """对同一业务批次重出：当前规则重出 + 留痕，落到同一批次的新账本版本。"""
    return execute(raw, params or {}, batch_ref=batch_ref, kind=kind, change_note=change_note)


def stage_preview(index, payload):
    if "rows" in payload and index > 0:
        if not isinstance(payload["rows"], list) or any(not isinstance(row, dict) for row in payload["rows"]):
            raise DomainError("BAD_ROWS", "记录需要对象列表")
        if not isinstance(payload.get("context", {}), dict):
            raise DomainError("BAD_CONTEXT", "上下文需要对象")
        return scalar(STAGES[index](payload["rows"], payload.get("context", {"parameters": {}, "quarantine": []})))
    raw = payload.get("csv")
    raw = raw.encode() if isinstance(raw, str) else load_sample(payload.get("sample", "dirty.csv"))
    return calculate(raw, payload.get("parameters", {}), index)["stages"][-1]
