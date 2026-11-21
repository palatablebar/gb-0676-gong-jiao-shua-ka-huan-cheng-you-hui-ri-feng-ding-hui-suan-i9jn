"""差异归因：沿同一来源行，对照原账与复算账。

每个差异被归入三类之一：
- DATA      补录/纠错：来源输入发生变化（同一来源行内容不同，或新增/移除来源行）；
- RULE      规则变化：输入一致，但适用规则版本不同导致金额变化；
- MIXED     两者同时发生（输入与规则都变），避免把数据错误误记成规则影响。
"""
from decimal import Decimal


def _accepted_index(run):
    """event_id -> 最终采纳行（M7 行携带 _sources 完整来源）。"""
    rows = run["stages"][-1]["rows"]
    return {r.get("event_id"): r for r in rows}


def _quarantine_index(run):
    return {q.get("event_id"): q for q in run["stages"][-1].get("cumulative_quarantine", []) if q.get("event_id")}


def _source_set(run):
    rows = run["stages"][-1]["rows"]
    quar = run["stages"][-1].get("cumulative_quarantine", [])
    return set(s for r in rows for s in r.get("_sources", [])) | set(
        s for q in quar for s in q.get("_sources", [])
    )


def _money(run, key):
    try:
        return Decimal(str(run["stages"][5]["metrics"].get(key, "0")))
    except (KeyError, IndexError):
        return Decimal("0")


def _classify(input_changed, rule_changed):
    if input_changed and rule_changed:
        return "MIXED"
    if input_changed:
        return "DATA"
    if rule_changed:
        return "RULE"
    return "NONE"


REASON_TEXT = {
    "DATA": "补录/数据纠错",
    "RULE": "规则变化",
    "MIXED": "数据与规则同时变化",
    "NONE": "无差异",
}


def compare(original, recalc):
    """返回逐行对照与批次级归因汇总。两份 run 必须是同一业务批次的不同账本版本。"""
    o_rows, r_rows = _accepted_index(original), _accepted_index(recalc)
    o_qua, r_qua = _quarantine_index(original), _quarantine_index(recalc)
    o_src, r_src = _source_set(original), _source_set(recalc)
    rule_changed = original["manifest"]["rule_version"] != recalc["manifest"]["rule_version"]

    lines = []
    for key in sorted(set(o_rows) | set(r_rows) | set(o_qua) | set(r_qua)):
        o, r = o_rows.get(key), r_rows.get(key)
        oq, rq = o_qua.get(key), r_qua.get(key)
        o_present = o is not None or oq is not None
        r_present = r is not None or rq is not None
        sources = sorted(set((o or r or oq or rq).get("_sources", [])))

        old_expected = (o or {}).get("expected")
        new_expected = (r or {}).get("expected")
        old_status = (o or {}).get("status") or ("隔离" if oq else None)
        new_status = (r or {}).get("status") or ("隔离" if rq else None)

        # 输入是否变化：来源行集合差异，或同键一边采纳一边隔离，或原始字段不同。
        input_changed = not (o_present and r_present)
        if o_present and r_present:
            input_changed = (o is None) != (r is None)  # 采纳/隔离状态翻转
        if o is not None and r is not None:
            for field in ("card", "route", "event_time", "result", "reported"):
                if str(o.get(field)) != str(r.get(field)):
                    input_changed = True

        money_changed = old_expected != new_expected or old_status != new_status
        cause = _classify(input_changed, rule_changed) if (input_changed or rule_changed) and money_changed else "NONE"
        if money_changed and cause == "NONE":
            cause = "DATA" if input_changed else ("RULE" if rule_changed else "NONE")

        lines.append({
            "event_id": key,
            "sources": sources,
            "original_status": old_status or "—",
            "recalc_status": new_status or "—",
            "original_expected": old_expected or "—",
            "recalc_expected": new_expected or "—",
            "input_changed": input_changed,
            "rule_changed": rule_changed,
            "cause": cause,
            "cause_text": REASON_TEXT[cause],
        })

    only_original_sources = sorted(o_src - r_src)
    only_recalc_sources = sorted(r_src - o_src)
    counts = {"DATA": 0, "RULE": 0, "MIXED": 0, "NONE": 0}
    for ln in lines:
        counts[ln["cause"]] += 1

    o_total, r_total = _money(original, "expected_total"), _money(recalc, "expected_total")
    return {
        "lines": lines,
        "only_original_sources": only_original_sources,
        "only_recalc_sources": only_recalc_sources,
        "counts": {k: v for k, v in counts.items() if k != "NONE"},
        "unchanged": counts["NONE"],
        "original_rule_version": original["manifest"]["rule_version"],
        "recalc_rule_version": recalc["manifest"]["rule_version"],
        "original_input_hash": original["manifest"]["input_sha256"],
        "recalc_input_hash": recalc["manifest"]["input_sha256"],
        "input_changed": original["manifest"]["input_sha256"] != recalc["manifest"]["input_sha256"],
        "rule_changed": rule_changed,
        "expected_total_delta": str(r_total - o_total),
        "original_expected_total": str(o_total),
        "recalc_expected_total": str(r_total),
    }
