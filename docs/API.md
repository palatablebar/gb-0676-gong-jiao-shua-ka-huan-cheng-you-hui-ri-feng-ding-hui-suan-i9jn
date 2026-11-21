# 接口

模块GET使用仓内样例，模块POST可传csv或rows与context，直接计算当前规则。/api/runs使用完整流水线并原子保存。每个阶段返回rows、quarantine、metrics；后续阶段还返回累计隔离。/api/upload接收multipart文件file。/api/runs/<id>/archive只导出一份带清单与证据的CSV。

## 规则登记簿

- `GET /api/rules`：返回当前生效规则版本 `current` 与全部冻结版本（生效窗、变化说明、规则集指纹）。规则只追加、不改写、不删除。

## 历史批次复算

一个业务批次（`batch_ref`）维护一条只追加的版本链，链上每个版本是一份完整、可独立读取的账本；批次头的 `current_run_id` 指向唯一当前生效结论。

- `POST /api/batches/<batch_ref>/recalc`：body 取 `sample` 或 `csv`，`parameters.rule_mode` 为 `as_of`（按乘车发生时规则原样重现）或 `current`（按当前规则重出，默认），可附 `rule_version` 显式钉版与 `change_note` 留痕。同输入+同规则+同参数的重试返回原版本，不新增账本版本；新输入或新规则才追加版本。已封存批次返回 409。
- `GET /api/batches`：业务批次列表（版本数、当前规则、复核与封存状态）。
- `GET /api/batches/<batch_ref>`：批次头与完整版本链（含每版完整 payload、规则变化说明、对平标记）。
- `GET /api/batches/<batch_ref>/compare`：沿同一来源行对照原账（首版）与当前复算账，逐行给出 `DATA`（补录/纠错）、`RULE`（规则变化）、`MIXED`（两者同时）归因及应扣总额差。
- `POST /api/batches/<batch_ref>/switch`：body `{run_id, expected_run_id}` 切换当前生效结论，乐观锁；`expected_run_id` 不匹配返回 409。切换不删除任何旧版，旧版仍可经 `GET /api/runs/<id>` 完整读取。
- `POST /api/batches/<batch_ref>/seal`：封存日结。当前版本来源未对平返回 422；封存后账本只读，拒绝追加与切换。
- `GET /api/batches/<batch_ref>/verify`：重算哈希链，校验原始输入摘要、规则依据指纹与前驱指针，报告复算链是否完好。

## 留档与防断链

`GET /api/runs/<id>/archive` 在原逐行证据（accepted/quarantine）之外，追加 `RULE_BASIS`（该版冻结规则快照）与 `RAW_INPUT`（原始 CSV 的 base64 留档及输入摘要）两段。系统不提供任何删除接口（DELETE 返回 405），原始输入与规则依据随账本永久留档，外部文件误删也不阻断复算链。
