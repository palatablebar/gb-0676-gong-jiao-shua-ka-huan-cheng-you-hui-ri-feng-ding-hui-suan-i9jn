"""历史批次复算：规则原样重现、当前规则重出、差异归因、版本链与封存门。"""
import csv
import io

from app import rule_registry, store
from app.attribution import compare
from app.pipeline import calculate, load_sample, recalc

B = "B-TEST-10"


def _legacy():
    return load_sample("month_legacy.csv")


def _corrected():
    return load_sample("month_corrected.csv")


# --------------------------------------------------------------- 规则登记簿

def test_registry_current_and_as_of():
    assert rule_registry.current() == "2026.11.01-r2"
    # 十月乘车落在 r1 生效窗
    assert rule_registry.as_of("2026-10-15T08:00:00+08:00") == "2026.01.01-r1"
    # 十一月乘车落在 r2 生效窗
    assert rule_registry.as_of("2026-11-16T08:00:00+08:00") == "2026.11.01-r2"


def test_registry_describe_change_lists_transfer_and_fare_diff():
    change = rule_registry.describe_change("2026.01.01-r1", "2026.11.01-r2")
    assert change["rule_changed"] is True
    params = {d["param"]: (d["old"], d["new"]) for d in change["diff"]}
    assert params["transfer_discount"] == ("1.00", "0.50")
    assert params["fare:F2"] == (None, "2.50")
    assert "F2" in change["changes"]


def test_unknown_rule_version_rejected():
    import pytest
    from app.errors import DomainError
    with pytest.raises(DomainError):
        rule_registry.get("not-a-version")


# --------------------------------------------------------------- 原样重现

def test_as_of_replay_uses_rule_at_ride_time():
    run = calculate(_legacy(), {"rule_mode": "as_of"})
    assert run["manifest"]["rule_version"] == "2026.01.01-r1"
    assert run["manifest"]["rule_mode"] == "as_of"
    # r1 票价 F1=2.00，换乘优惠 1.00
    fares = {r["event_id"]: r for r in run["stages"][4]["rows"]}
    assert fares["0100"]["base_fare"] == "2.00"
    assert fares["0101"]["transfer_discount"] == "1.00"


def test_current_recalc_uses_latest_rule():
    run = calculate(_legacy(), {"rule_mode": "current"})
    assert run["manifest"]["rule_version"] == rule_registry.current()
    fares = {r["event_id"]: r for r in run["stages"][4]["rows"]}
    # r2 换乘优惠下调为 0.50
    assert fares["0101"]["transfer_discount"] == "0.50"


def test_pinned_rule_version_overrides_mode():
    run = calculate(_legacy(), {"rule_mode": "current", "rule_version": "2026.01.01-r1"})
    assert run["manifest"]["rule_version"] == "2026.01.01-r1"


# --------------------------------------------------------------- 账本版本

def test_retry_same_attempt_adds_no_version():
    first = recalc(B, _legacy(), {"rule_mode": "as_of"}, kind="原账", change_note="首次入账")
    recalc(B, _legacy(), {"rule_mode": "as_of"})  # 同输入同规则重试
    batch = store.get_batch(B)
    assert len(batch["versions"]) == 1
    assert batch["current_run_id"] == first["run_id"]


def test_rule_change_appends_version_and_data_change_appends_another():
    recalc(B, _legacy(), {"rule_mode": "as_of"}, kind="原账")
    recalc(B, _legacy(), {"rule_mode": "current"}, kind="规则复算")
    recalc(B, _corrected(), {"rule_mode": "current"}, kind="纠错复算")
    batch = store.get_batch(B)
    assert [v["ledger_version"] for v in batch["versions"]] == [1, 2, 3]
    assert [v["rule_version"] for v in batch["versions"]] == [
        "2026.01.01-r1", "2026.11.01-r2", "2026.11.01-r2"]
    # 最新版本成为唯一当前生效结论
    assert batch["current_run_id"] == batch["versions"][-1]["run_id"]


def test_each_batch_has_single_current_conclusion():
    recalc(B, _legacy(), {"rule_mode": "as_of"})
    recalc(B, _legacy(), {"rule_mode": "current"})
    batch = store.get_batch(B)
    currents = [v for v in batch["versions"] if v["run_id"] == batch["current_run_id"]]
    assert len(currents) == 1


# --------------------------------------------------------------- 差异归因

def test_rule_only_change_attributed_to_rule_not_data():
    original = calculate(_legacy(), {"rule_mode": "as_of"})
    recalc_run = calculate(_legacy(), {"rule_mode": "current"})
    result = compare(original, recalc_run)
    assert result["input_changed"] is False
    assert result["rule_changed"] is True
    assert result["counts"]["DATA"] == 0
    assert result["counts"]["RULE"] > 0
    assert result["counts"]["MIXED"] == 0
    # 三笔跨线换乘，每笔优惠少 0.50 → 应扣总额增加 1.50
    assert result["expected_total_delta"] == "1.50"


def test_data_correction_is_distinguished_from_rule_change():
    original = calculate(_legacy(), {"rule_mode": "as_of"})
    corrected = calculate(_corrected(), {"rule_mode": "current"})
    result = compare(original, corrected)
    assert result["input_changed"] is True
    # 补录的新来源行（0112 在第 8 行）
    assert 8 in result["only_recalc_sources"]
    # 0112 为补录数据，0190 由隔离转采纳 → 存在数据/混合归因
    assert result["counts"]["DATA"] + result["counts"]["MIXED"] > 0


# --------------------------------------------------------------- 版本切换

def test_stale_switch_rejected_but_old_version_remains_readable():
    import pytest
    from app.errors import DomainError
    recalc(B, _legacy(), {"rule_mode": "as_of"})
    v2 = recalc(B, _legacy(), {"rule_mode": "current"})
    batch = store.get_batch(B)
    original_id = batch["versions"][0]["run_id"]
    # 错误的 expected_run_id → 409，旧版不被覆盖
    with pytest.raises(DomainError) as info:
        store.switch_current(B, original_id, expected_run_id="stale-id")
    assert info.value.code == "STALE_SWITCH"
    # 旧版本仍可完整读取（含全部 7 个阶段）
    old = store.get(original_id)
    assert len(old["stages"]) == 7
    # 正确切换成功
    switched = store.switch_current(B, original_id, expected_run_id=v2["run_id"])
    assert switched["current_run_id"] == original_id


def test_switch_rejects_version_from_other_batch():
    import pytest
    from app.errors import DomainError
    recalc(B, _legacy(), {"rule_mode": "as_of"})
    other = recalc("B-OTHER", _corrected(), {"rule_mode": "current"})
    with pytest.raises(DomainError) as info:
        store.switch_current(B, other["run_id"])
    assert info.value.code == "NOT_MEMBER"


# --------------------------------------------------------------- 封存门与留档

def test_unbalanced_batch_cannot_be_sealed():
    import pytest
    from app.errors import DomainError
    run = calculate(_legacy(), {"rule_mode": "as_of", "marker": "unbalanced"})
    run["stages"][5]["metrics"]["balanced"] = False
    store.persist(run, batch_ref="B-UNBAL", kind="原账")
    with pytest.raises(DomainError) as info:
        store.seal("B-UNBAL")
    assert info.value.code == "UNBALANCED_SEAL"
    assert store.get_batch("B-UNBAL")["sealed"] is False


def test_sealed_batch_rejects_new_recalc():
    import pytest
    from app.errors import DomainError
    recalc("B-SEAL", _legacy(), {"rule_mode": "as_of"})
    store.seal("B-SEAL")
    with pytest.raises(DomainError) as info:
        recalc("B-SEAL", _corrected(), {"rule_mode": "current"})
    assert info.value.code == "BATCH_SEALED"


def test_sealed_batch_rejects_review_rewrite():
    import pytest
    from app.errors import DomainError
    run = recalc("B-SEAL-REVIEW", _legacy(), {"rule_mode": "as_of"})
    store.seal("B-SEAL-REVIEW")
    with pytest.raises(DomainError) as info:
        store.review(run["run_id"], run["version"], "退回复核")
    assert info.value.code == "BATCH_SEALED"


def test_chain_verification_detects_tampering():
    recalc("B-CHAIN", _legacy(), {"rule_mode": "as_of"})
    recalc("B-CHAIN", _legacy(), {"rule_mode": "current"})
    assert store.verify_chain("B-CHAIN")["intact"] is True
    # 直接篡改 runs 留档 → 哈希链应报警
    with store.connect() as db:
        row = db.execute("SELECT payload FROM runs LIMIT 1").fetchone()
        import json as _json
        payload = _json.loads(row[0])
        payload["manifest"]["note"] = "tampered"
        db.execute("UPDATE runs SET payload=? WHERE id=?",
                   (_json.dumps(payload, ensure_ascii=False), payload["run_id"]))
    result = store.verify_chain("B-CHAIN")
    assert result["intact"] is False
    assert any("哈希" in p for p in result["problems"])


def test_archive_retains_raw_input_and_rule_basis():
    run = recalc("B-ARCH", _legacy(), {"rule_mode": "as_of"})
    from app.archive import build_archive
    csv_text = build_archive(store.get(run["run_id"]))
    assert "RULE_BASIS" in csv_text
    assert "RAW_INPUT" in csv_text
    assert run["manifest"]["rule_version"] in csv_text
    # 段一仍保留逐行证据与隔离行
    assert "quarantine" in csv_text
    rows = list(csv.reader(io.StringIO(csv_text.encode().decode("utf-8-sig"))))
    assert rows[0][1] == "input_sha256"


# --------------------------------------------------------------- HTTP 接口

def test_http_full_recalc_workflow(client):
    # 规则登记簿
    catalog = client.get("/api/rules").get_json()
    assert catalog["current"] == rule_registry.current()

    r = client.post(f"/api/batches/{B}/recalc",
                    json={"sample": "month_legacy.csv", "parameters": {"rule_mode": "as_of"}, "change_note": "原账"})
    assert r.status_code == 200 and r.get_json()["manifest"]["rule_version"] == "2026.01.01-r1"

    # 重试不新增版本
    client.post(f"/api/batches/{B}/recalc",
                json={"sample": "month_legacy.csv", "parameters": {"rule_mode": "as_of"}})
    assert len(client.get(f"/api/batches/{B}").get_json()["versions"]) == 1

    client.post(f"/api/batches/{B}/recalc",
                json={"sample": "month_corrected.csv", "parameters": {"rule_mode": "current"}, "change_note": "补录"})
    batch = client.get(f"/api/batches/{B}").get_json()
    assert len(batch["versions"]) == 2

    cmp_resp = client.get(f"/api/batches/{B}/compare").get_json()["comparison"]
    assert "lines" in cmp_resp and cmp_resp["input_changed"] is True

    # 乐观锁：陈旧切换 409
    first_id = batch["versions"][0]["run_id"]
    stale = client.post(f"/api/batches/{B}/switch",
                        json={"run_id": first_id, "expected_run_id": "wrong"})
    assert stale.status_code == 409

    # 封存
    sealed = client.post(f"/api/batches/{B}/seal")
    assert sealed.status_code == 200 and sealed.get_json()["sealed"] is True
    locked = client.post(f"/api/batches/{B}/recalc",
                         json={"sample": "month_corrected.csv", "parameters": {}})
    assert locked.status_code == 409


def test_recalc_page_renders(client):
    page = client.get("/recalc")
    assert page.status_code == 200 and "历史批次复算".encode() in page.data


def test_no_delete_route(client):
    recalc(B, _legacy(), {"rule_mode": "as_of"})
    batch = store.get_batch(B)
    run_id = batch["versions"][0]["run_id"]
    assert client.delete(f"/api/runs/{run_id}").status_code == 405
