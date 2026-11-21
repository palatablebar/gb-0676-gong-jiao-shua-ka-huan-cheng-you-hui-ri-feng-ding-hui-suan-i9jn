import base64
import hashlib
import json
import time
from datetime import datetime, timezone
from app.rule_registry import ruleset_hash


def digest(raw, params=None, rule_version=None):
    # 规则版本纳入指纹：同输入在不同规则下产生不同 run_id（不同账本版本）。
    return hashlib.sha256(
        raw
        + json.dumps(params or {}, sort_keys=True, ensure_ascii=False).encode()
        + (rule_version or "").encode()
    ).hexdigest()


def manifest(raw, params, started, rule_version, rule_mode="current"):
    """运行清单：原始输入留档 + 规则依据留档，断链可凭此重验。"""
    rules = rule_registry_snapshot(rule_version, params)
    return {
        "input_sha256": hashlib.sha256(raw).hexdigest(),
        "input_raw_base64": base64.b64encode(raw).decode(),
        "rule_version": rule_version,
        "rule_mode": rule_mode,
        "ruleset_hash": ruleset_hash(rules),
        "rule_basis": rules,
        "parameters": {k: v for k, v in (params or {}).items() if k != "fares"},
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
    }


def rule_registry_snapshot(rule_version, params):
    """该次计算实际生效的规则快照（票价表 + 优惠参数）。"""
    from app.rule_registry import rules_of

    frozen = dict(rules_of(rule_version))
    return {
        "fares": params.get("fares") or frozen["fares"],
        "daily_cap": params.get("daily_cap", frozen["daily_cap"]),
        "transfer_discount": params.get("transfer_discount", frozen["transfer_discount"]),
        "transfer_minutes": params.get("transfer_minutes", frozen["transfer_minutes"]),
    }
