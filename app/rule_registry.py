"""规则登记簿（append-only，不可改写）。

每条规则版本在其生效窗内冻结一份完整规则集（票价表 + 优惠参数 + 适用线路），
并携带人可读的变化说明。历史批次复算可：
- 按乘车发生时间原样重现（as_of -> 当时生效的规则版本）；
- 按当前规则重出（current -> 最新规则版本）。

规则一经登记只能新增、不能修改或删除，以保证复算链可被重新验证。
"""
import hashlib
import json
from datetime import datetime, timezone
from app.errors import DomainError
from app.values import instant

# 冻结的规则版本。interval 统一 [effective_from, effective_to)，None 表示至今。
# 变化说明 changes 描述相对上一版的差异，供页面“规则变化说明”直接展示。
RULE_VERSIONS = [
    {
        "rule_version": "2026.01.01-r1",
        "effective_from": "2026-01-01T00:00:00+08:00",
        "effective_to": "2026-11-01T00:00:00+08:00",
        "changes": "首版规则：基础票价F1=2.00元；跨线换乘优惠1.00元（60分钟窗，恰60分钟排除）；卡号业务日封顶6.00元。",
        "rules": {
            "fares": [
                {"start": "2026-01-01T00:00:00+08:00", "end": "2026-11-01T00:00:00+08:00", "version": "F1", "amount": "2.00"},
            ],
            "daily_cap": "6.00",
            "transfer_discount": "1.00",
            "transfer_minutes": "60",
        },
    },
    {
        "rule_version": "2026.11.01-r2",
        "effective_from": "2026-11-01T00:00:00+08:00",
        "effective_to": None,
        "changes": "票价调整：新增F2=2.50元并对2026-11-01后乘车生效（F1同时停用）；换乘优惠由1.00元下调为0.50元；日封顶6.00元与60分钟换乘窗不变。",
        "rules": {
            "fares": [
                {"start": "2026-01-01T00:00:00+08:00", "end": "2026-11-01T00:00:00+08:00", "version": "F1", "amount": "2.00"},
                {"start": "2026-11-01T00:00:00+08:00", "end": "2027-01-01T00:00:00+08:00", "version": "F2", "amount": "2.50"},
            ],
            "daily_cap": "6.00",
            "transfer_discount": "0.50",
            "transfer_minutes": "60",
        },
    },
]

CURRENT_RULE_VERSION = RULE_VERSIONS[-1]["rule_version"]


def _canonical(rules):
    return json.dumps(rules, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def ruleset_hash(rules):
    """规则集内容指纹；同一指纹代表同一份规则，重试不产生新版本。"""
    return hashlib.sha256(_canonical(rules).encode()).hexdigest()[:16]


def all_versions():
    return [
        {
            "rule_version": v["rule_version"],
            "effective_from": v["effective_from"],
            "effective_to": v["effective_to"],
            "changes": v["changes"],
            "ruleset_hash": ruleset_hash(v["rules"]),
        }
        for v in RULE_VERSIONS
    ]


def get(rule_version):
    for v in RULE_VERSIONS:
        if v["rule_version"] == rule_version:
            return v
    raise DomainError("UNKNOWN_RULE_VERSION", f"规则版本 {rule_version} 未登记", 404)


def rules_of(rule_version):
    return get(rule_version)["rules"]


def as_of(at_value):
    """返回乘车发生时刻 at 生效的规则版本（原样重现依据）。"""
    at = instant(at_value) if not isinstance(at_value, datetime) else at_value.astimezone(timezone.utc)
    hits = [
        v for v in RULE_VERSIONS
        if instant(v["effective_from"]) <= at and (v["effective_to"] is None or at < instant(v["effective_to"]))
    ]
    if len(hits) != 1:
        raise DomainError("RULE_WINDOW_GAP", "该时刻没有唯一生效的规则版本，无法原样重现")
    return hits[0]["rule_version"]


def current():
    return CURRENT_RULE_VERSION


def describe_change(old_version, new_version):
    """两版规则的结构化差异 + 新版变化说明，供可追溯展示。"""
    if old_version is None:
        return {"rule_changed": False, "from": None, "to": new_version, "changes": get(new_version)["changes"], "diff": []}
    if old_version == new_version:
        return {"rule_changed": False, "from": old_version, "to": new_version, "changes": "", "diff": []}
    old = rules_of(old_version)
    new = rules_of(new_version)
    diff = []
    for key in ("daily_cap", "transfer_discount", "transfer_minutes"):
        if str(old.get(key)) != str(new.get(key)):
            diff.append({"param": key, "old": old.get(key), "new": new.get(key)})
    old_fares = {f["version"]: f["amount"] for f in old["fares"]}
    new_fares = {f["version"]: f["amount"] for f in new["fares"]}
    for code in sorted(set(old_fares) | set(new_fares)):
        if old_fares.get(code) != new_fares.get(code):
            diff.append({"param": f"fare:{code}", "old": old_fares.get(code), "new": new_fares.get(code)})
    return {
        "rule_changed": True,
        "from": old_version,
        "to": new_version,
        "changes": get(new_version)["changes"],
        "diff": diff,
    }
