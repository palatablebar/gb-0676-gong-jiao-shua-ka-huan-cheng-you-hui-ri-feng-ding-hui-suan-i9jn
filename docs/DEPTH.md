# 深度登记 公交刷卡换乘优惠与日封顶回算

规模：中型。使用者：公交清分中心复核员。核心规则：换乘按上次有效付费行程的时间窗计算，优惠与日封顶按确定顺序裁决并保留分币守恒。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 刷卡采信 | 查看并运行刷卡采信 | `/api/taps` | `app/modules/m1_taps/rules.py` | `tests/test_m1.py` |
| M2 线路换乘 | 查看并运行线路换乘 | `/api/routes` | `app/modules/m2_routes/rules.py` | `tests/test_m2.py` |
| M3 票价版本 | 查看并运行票价版本 | `/api/fares` | `app/modules/m3_fares/rules.py` | `tests/test_m3.py` |
| M4 行程配对 | 查看并运行行程配对 | `/api/journeys` | `app/modules/m4_journeys/rules.py` | `tests/test_m4.py` |
| M5 优惠回算 | 查看并运行优惠回算 | `/api/discounts` | `app/modules/m5_discounts/rules.py` | `tests/test_m5.py` |
| M6 清分对账 | 查看并运行清分对账 | `/api/clearing` | `app/modules/m6_clearing/rules.py` | `tests/test_m6.py` |
| M7 日结归档 | 查看并运行日结归档 | `/api/settlement` | `app/modules/m7_settlement/rules.py` | `tests/test_m7.py` |
| 历史批次复算 | 原账重现、当前规则重出、差异归因、版本切换与封存 | `/api/batches`、`/recalc` | `app/rule_registry.py`、`app/attribution.py`、`app/store.py` | `tests/test_recalc.py` |

## 陷阱登记

| TRAP | 现象 | 朴素实现 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-01 | 旧编码 | 默认解码失败 | 回退GB18030 | `app/modules/m1_taps/rules.py` | `tests/test_m1.py::test_legacy_encoding_real_sample` |
| TRAP-02 | BOM | 首列名多字符 | utf-8-sig | `app/modules/m1_taps/rules.py` | `tests/test_m1.py::test_bom_real_sample` |
| TRAP-03 | 带引号换行 | 拆成两条 | csv逻辑行 | `app/modules/m1_taps/rules.py` | `tests/test_m1.py::test_quoted_newline_is_one_record` |
| TRAP-05 | 列数不匹配 | 静默错列 | 隔离原行 | `app/modules/m1_taps/rules.py` | `tests/test_m1.py::test_bad_columns_real_sample` |
| TRAP-08 | 前导零ID | 编码变形 | 全程字符串 | `app/modules/m1_taps/rules.py` | `tests/test_m1.py::test_identifier_preserved_real_sample` |
| TRAP-14 | 重复键冲突 | 后写覆盖 | 保留首条并隔离冲突行 | `app/modules/m1_taps/rules.py` | `tests/test_m1.py::test_duplicate_conflict_real_sample` |
| TRAP-18 | 空态三态 | 一律成功 | 分别错误码 | `app/modules/m1_taps/rules.py` | `tests/test_m1.py::test_three_empty_states` |
| TRAP-26 | 旧版本提交 | 覆盖新结果 | 版本条件更新 | `app/store.py` | `tests/test_e2e.py::test_stale_review_409` |
| TRAP-06 | 全角数字 | 转换失败 | NFKC与Decimal | `app/values.py` | `tests/test_m3.py::test_thousands_and_fullwidth` |
| TRAP-07 | 金额哨兵 | 零参与扣费 | 空值隔离 | `app/values.py` | `tests/test_m3.py::test_real_sentinel_quarantined` |
| TRAP-11 | 混合偏移 | 落错版本 | UTC归整 | `app/values.py` | `tests/test_m3.py::test_timezone_equal` |
| TRAP-23 | 换乘截止点 | 重复优惠 | 截止点排除 | `app/modules/m5_discounts/rules.py` | `tests/test_m5.py::test_exact_window_excluded` |
| TRAP-25 | 票价重叠 | 随意取版 | 冲突隔离 | `app/modules/m3_fares/rules.py` | `tests/test_m3.py::test_overlap_rejected` |
| TRAP-30 | 新旧规则混淆 | 一套当前结果无法区分差异来源 | 规则登记簿按生效窗冻结，as_of原样重现/current重出，逐行归因DATA/RULE/MIXED | `app/rule_registry.py`、`app/attribution.py` | `tests/test_recalc.py` |
| TRAP-31 | 重试产生冗余版本 | 同批重跑版本膨胀 | attempt_id（输入+规则+参数指纹）唯一，重复提交复用原版本 | `app/store.py` | `tests/test_recalc.py::test_retry_same_attempt_adds_no_version` |
| TRAP-32 | 切换覆盖旧版 | 旧结论丢失不可读 | 只改批次头指针，旧版完整payload保留，乐观锁409 | `app/store.py` | `tests/test_recalc.py::test_stale_switch_rejected_but_old_version_remains_readable` |
| TRAP-33 | 未对平即封存 | 日结带着差额封存 | seal校验balanced，未对平422 | `app/store.py` | `tests/test_recalc.py::test_unbalanced_batch_cannot_be_sealed` |
| TRAP-34 | 留档被篡改/断链 | 复算链静默失真 | 哈希链+输入摘要+规则指纹，/verify重算校验，无删除接口 | `app/store.py`、`app/archive.py` | `tests/test_recalc.py::test_chain_verification_detects_tampering` |

## 对账规则

输入原始逻辑行=最终采纳来源行+隔离来源行+重复行+空白行+分段表头。派生记录用_sources保存完整来源，因此分组不丢行。业务预期合计与上游申报合计之差等于逐行差异之和。结果按输入摘要、规则版本及参数产生唯一批次，事务写入；同源重跑只复用已有批次。

历史批次以 `batch_ref` 归并为一条只追加账本：原账按乘车时规则（as_of）冻结，复算账按当前规则（current）重出并保留原账；`attempt_id` 为输入+规则+参数指纹，同批重试不新增版本。批次头 `current_run_id` 是唯一当前生效结论，切换只动指针、不删旧版；封存要求当前版本对平，封存后只读。每版经 `chain_hash=sha256(prev_hash||payload)` 串成哈希链，原始输入与规则依据随清单留档，`/verify` 可重算验链。

## 已知不覆盖

4MB以上文件需预先按业务键分批；不模拟生产支付与外部回调。人工复核只变更批次状态，不改写原始记录。
