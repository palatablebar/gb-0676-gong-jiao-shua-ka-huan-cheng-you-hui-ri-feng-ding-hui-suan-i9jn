import csv
import io
import json


def build_archive(run):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    meta = run["manifest"]

    # 段一：逐行证据（保持原有表头与列序，旧测试与旧工具可继续读取）。
    writer.writerow(["run_id", "input_sha256", "rule_version", "kind", "source_lines", "reason", "evidence"])
    for row in run["stages"][-1]["rows"]:
        writer.writerow([
            run["run_id"], meta["input_sha256"], meta["rule_version"], "accepted",
            json.dumps(row.get("_sources", [])), row.get("status", ""), json.dumps(row, ensure_ascii=False),
        ])
    for row in run["stages"][-1].get("cumulative_quarantine", []):
        writer.writerow([
            run["run_id"], meta["input_sha256"], meta["rule_version"], "quarantine",
            json.dumps(row.get("_sources", [])), row.get("reason", ""), json.dumps(row, ensure_ascii=False),
        ])

    # 段二：规则依据留档，保证历史账本可按当时规则原样重现。
    writer.writerow([])
    writer.writerow(["section", "rule_version", "rule_mode", "ruleset_hash", "rule_basis", "changes"])
    basis = meta.get("rule_basis", {})
    writer.writerow([
        "RULE_BASIS", meta["rule_version"], meta.get("rule_mode", ""),
        meta.get("ruleset_hash", ""), json.dumps(basis, ensure_ascii=False), "",
    ])

    # 段三：原始输入留档（base64 原样保存，误删外部文件也不阻断复算链）。
    writer.writerow([])
    writer.writerow(["section", "input_sha256", "encoding", "raw_base64"])
    writer.writerow(["RAW_INPUT", meta["input_sha256"], "base64", meta.get("input_raw_base64", "")])
    return "﻿" + stream.getvalue()
