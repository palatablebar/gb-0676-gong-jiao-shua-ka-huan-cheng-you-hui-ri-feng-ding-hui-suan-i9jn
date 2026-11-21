import hashlib
import json
import sqlite3
from app.config import DATA_DIR
from app.errors import DomainError
from app.rule_registry import rules_of, ruleset_hash, describe_change


def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATA_DIR / "records.db", timeout=20)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute(
        "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY,payload TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,decision TEXT NOT NULL DEFAULT '待复核')"
    )
    # 业务批次：一个稳定业务键对应一行，current_run_id 指向唯一当前生效结论。
    db.execute(
        "CREATE TABLE IF NOT EXISTS batches ("
        "batch_ref TEXT PRIMARY KEY, current_run_id TEXT, sealed INTEGER NOT NULL DEFAULT 0,"
        "sealed_at TEXT, created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')))"
    )
    # 批次账本：只追加。同一 attempt（同输入+同规则+同参数）重试返回原行，不新增版本。
    db.execute(
        "CREATE TABLE IF NOT EXISTS batch_versions ("
        "batch_ref TEXT NOT NULL, run_id TEXT NOT NULL, ledger_version INTEGER NOT NULL,"
        "input_hash TEXT NOT NULL, rule_version TEXT NOT NULL, ruleset_hash TEXT NOT NULL,"
        "attempt_id TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, change_note TEXT,"
        "prev_hash TEXT, chain_hash TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),"
        "UNIQUE(batch_ref, ledger_version), UNIQUE(batch_ref, run_id), PRIMARY KEY(batch_ref, attempt_id))"
    )
    return db


def _balanced(payload):
    try:
        return bool(payload["stages"][5]["metrics"].get("balanced", False))
    except (KeyError, IndexError, TypeError):
        return False


def _chain_hash(prev_hash, payload):
    body = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256((prev_hash or "GENESIS").encode() + body.encode()).hexdigest()


def get(run_id):
    with connect() as db:
        row = db.execute("SELECT payload,version,decision FROM runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        raise DomainError("NOT_FOUND", "批次不存在", 404)
    payload = json.loads(row[0])
    payload["version"] = row[1]
    payload["decision"] = row[2]
    return payload


def save(run_id, payload):
    """兼容旧入口：以输入哈希作为默认业务批次落账。"""
    return persist(payload, batch_ref=None, kind="复算", change_note="")


def persist(payload, batch_ref=None, kind="复算", change_note=""):
    """原子写入一次计算并登记到批次账本。

    - 同 attempt（输入+规则+参数指纹相同）重试：复用既有版本，不新增 ledger_version。
    - 新输入或新规则：在同一 batch_ref 下追加一个新版本。
    - 批次封存后拒绝任何追加，从源头保证封存日结不可被改写。
    """
    run_id = payload["run_id"]
    meta = payload["manifest"]
    input_hash = meta["input_sha256"]
    rule_version = meta["rule_version"]
    r_hash = ruleset_hash(rules_of(rule_version))
    attempt_id = run_id
    batch_ref = batch_ref or input_hash

    with connect() as db:
        sealed = db.execute("SELECT sealed FROM batches WHERE batch_ref=?", (batch_ref,)).fetchone()
        if sealed and sealed[0]:
            raise DomainError("BATCH_SEALED", "批次已封存，不能再追加复算版本", 409)

        existing = db.execute(
            "SELECT run_id FROM batch_versions WHERE batch_ref=? AND attempt_id=?", (batch_ref, attempt_id)
        ).fetchone()
        if existing:
            return get(existing[0])

        db.execute("INSERT OR IGNORE INTO runs(id,payload) VALUES (?,?)", (run_id, json.dumps(payload, ensure_ascii=False)))
        prev = db.execute(
            "SELECT chain_hash,ledger_version FROM batch_versions WHERE batch_ref=? ORDER BY ledger_version DESC LIMIT 1",
            (batch_ref,),
        ).fetchone()
        prev_hash, ledger_version = (prev[0], prev[1] + 1) if prev else (None, 1)
        chain_hash = _chain_hash(prev_hash, payload)
        db.execute(
            "INSERT INTO batch_versions(batch_ref,run_id,ledger_version,input_hash,rule_version,ruleset_hash,"
            "attempt_id,kind,change_note,prev_hash,chain_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (batch_ref, run_id, ledger_version, input_hash, rule_version, r_hash, attempt_id, kind, change_note, prev_hash, chain_hash),
        )
        db.execute(
            "INSERT INTO batches(batch_ref,current_run_id) VALUES (?,?) "
            "ON CONFLICT(batch_ref) DO UPDATE SET current_run_id=excluded.current_run_id",
            (batch_ref, run_id),
        )
    return get(run_id)


def latest():
    with connect() as db:
        row = db.execute("SELECT id FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
    return get(row[0]) if row else None


def history():
    with connect() as db:
        return [
            {"run_id": i, "version": v, "decision": d}
            for i, v, d in db.execute("SELECT id,version,decision FROM runs ORDER BY rowid DESC")
        ]


def review(run_id, version, decision):
    if decision not in {"已复核", "退回复核"}:
        raise DomainError("BAD_DECISION", "复核状态无效")
    with connect() as db:
        # 封存日结只读：其账本版本不得再被复核改写。
        sealed = db.execute(
            "SELECT 1 FROM batches b JOIN batch_versions v ON v.batch_ref=b.batch_ref "
            "WHERE v.run_id=? AND b.sealed=1", (run_id,)
        ).fetchone()
        if sealed:
            raise DomainError("BATCH_SEALED", "批次已封存，复核结论锁定", 409)
        cursor = db.execute(
            "UPDATE runs SET version=version+1,decision=? WHERE id=? AND version=?", (decision, run_id, version)
        )
        if cursor.rowcount != 1:
            if not db.execute("SELECT 1 FROM runs WHERE id=?", (run_id,)).fetchone():
                raise DomainError("NOT_FOUND", "批次不存在", 404)
            raise DomainError("STALE_VERSION", "版本已更新，请刷新", 409)
    return get(run_id)


# ---------------------------------------------------------------- 批次视图


def list_batches():
    with connect() as db:
        rows = db.execute(
            "SELECT b.batch_ref,b.current_run_id,b.sealed,b.sealed_at,"
            "COUNT(v.run_id) FROM batches b LEFT JOIN batch_versions v ON v.batch_ref=b.batch_ref "
            "GROUP BY b.batch_ref ORDER BY b.rowid DESC"
        ).fetchall()
    out = []
    for ref, current_id, sealed, sealed_at, count in rows:
        run = get(current_id)
        out.append({
            "batch_ref": ref,
            "current_run_id": current_id,
            "ledger_versions": count,
            "sealed": bool(sealed),
            "sealed_at": sealed_at,
            "decision": run["decision"],
            "current_rule_version": run["manifest"]["rule_version"],
            "input_sha256": run["manifest"]["input_sha256"],
        })
    return out


def get_batch(batch_ref):
    """返回批次头 + 完整版本链（每个版本含完整 payload，旧版始终可读）。"""
    with connect() as db:
        head = db.execute(
            "SELECT current_run_id,sealed,sealed_at FROM batches WHERE batch_ref=?", (batch_ref,)
        ).fetchone()
        if not head:
            raise DomainError("NOT_FOUND", "业务批次不存在", 404)
        version_rows = db.execute(
            "SELECT run_id,ledger_version,input_hash,rule_version,ruleset_hash,attempt_id,kind,"
            "change_note,prev_hash,chain_hash,created_at FROM batch_versions WHERE batch_ref=? ORDER BY ledger_version",
            (batch_ref,),
        ).fetchall()

    versions = []
    for i, vr in enumerate(version_rows):
        run = get(vr[0])
        prev_rule = version_rows[i - 1][3] if i else None
        versions.append({
            "run_id": vr[0],
            "ledger_version": vr[1],
            "input_hash": vr[2],
            "rule_version": vr[3],
            "ruleset_hash": vr[4],
            "attempt_id": vr[5],
            "kind": vr[6],
            "change_note": vr[7],
            "prev_hash": vr[8],
            "chain_hash": vr[9],
            "created_at": vr[10],
            "version": run["version"],
            "decision": run["decision"],
            "rule_change": describe_change(prev_rule, vr[3]),
            "balanced": _balanced(run),
            "manifest": run["manifest"],
            "payload": run,
        })
    return {
        "batch_ref": batch_ref,
        "current_run_id": head[0],
        "sealed": bool(head[1]),
        "sealed_at": head[2],
        "versions": versions,
    }


def switch_current(batch_ref, run_id, expected_run_id=None):
    """切换当前生效结论（乐观锁）。失败不删除、不覆盖任何旧版，旧版仍可完整读取。"""
    with connect() as db:
        head = db.execute("SELECT current_run_id,sealed FROM batches WHERE batch_ref=?", (batch_ref,)).fetchone()
        if not head:
            raise DomainError("NOT_FOUND", "业务批次不存在", 404)
        if head[1]:
            raise DomainError("BATCH_SEALED", "批次已封存，当前结论锁定", 409)
        if expected_run_id is not None and head[0] != expected_run_id:
            raise DomainError("STALE_SWITCH", "当前结论已被他人切换，请刷新后重试", 409)
        member = db.execute(
            "SELECT 1 FROM batch_versions WHERE batch_ref=? AND run_id=?", (batch_ref, run_id)
        ).fetchone()
        if not member:
            raise DomainError("NOT_MEMBER", "该版本不属于此业务批次", 422)
        db.execute("UPDATE batches SET current_run_id=? WHERE batch_ref=?", (run_id, batch_ref))
    return get_batch(batch_ref)


def seal(batch_ref):
    """封存日结：来源未对平不得封存。封存后账本只读。"""
    batch = get_batch(batch_ref)
    current = next(v for v in batch["versions"] if v["run_id"] == batch["current_run_id"])
    if not current["balanced"]:
        raise DomainError("UNBALANCED_SEAL", "来源未对平，不得封存日结", 422)
    with connect() as db:
        cursor = db.execute(
            "UPDATE batches SET sealed=1,sealed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
            "WHERE batch_ref=? AND sealed=0",
            (batch_ref,),
        )
        if cursor.rowcount != 1:
            raise DomainError("BATCH_SEALED", "批次已封存", 409)
    return get_batch(batch_ref)


def _raw_payload(db, run_id):
    row = db.execute("SELECT payload FROM runs WHERE id=?", (run_id,)).fetchone()
    return json.loads(row[0]) if row else None


def verify_chain(batch_ref):
    """重算哈希链，验证原始输入与规则依据留档未被篡改、复算链未断裂。"""
    batch = get_batch(batch_ref)
    prev_hash = None
    problems = []
    with connect() as db:
        for v in batch["versions"]:
            payload = _raw_payload(db, v["run_id"])
            recomputed = _chain_hash(prev_hash, payload)
            if v["prev_hash"] != prev_hash:
                problems.append(f"V{v['ledger_version']} 前驱指针断裂")
            if v["chain_hash"] != recomputed:
                problems.append(f"V{v['ledger_version']} 内容哈希不匹配（留档可能被改动）")
            if v["ruleset_hash"] != ruleset_hash(rules_of(v["rule_version"])):
                problems.append(f"V{v['ledger_version']} 规则依据与登记簿不一致")
            if payload["manifest"]["input_sha256"] != v["input_hash"]:
                problems.append(f"V{v['ledger_version']} 原始输入摘要不匹配")
            prev_hash = v["chain_hash"]
    return {"batch_ref": batch_ref, "intact": not problems, "problems": problems, "checked": len(batch["versions"])}
