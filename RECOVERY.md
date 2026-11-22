# 外站换盘恢复

白班供数、冻结、外发和外站接办沿用 README.md。操作员在外站盘之外保存一份固定的保护外发号名单，更换备份后原样提供。名单适用于同一外站，名单内日报在首次保护领取前已按原流程接办并过了撤发期，且此前未走普通领取。名单内日报的旧备份可以早于其接办。保管库独立于外站盘，换盘时仍保留。调用串行，操作员在领取失答时先原样重试或重新恢复，再开始新的领取。源仓在首次保护领取前即可关闭。

## 调用

- `delivery.claim.configure_protection(receiver, dispatch_ids)` 在 `initialize_delivery` 之后调用。`dispatch_ids` 是非空的外发号序列，每个标识唯一。同一外站的保护名单固定。开站或换盘时，操作员在开放领取前调用本入口，再执行恢复。
- `delivery.claim.restore_protected(receiver, call_custody, *, checkpoint=None)` 从独立保管端取得该外站及名单的已确认事实，恢复正式日报、首执和所有成功请求的绑定。返回 `None`。首次启用时可以没有确认记录。把就绪和整批恢复一并提交。恢复提交前调用 `checkpoint("before_restore_commit")`，回调抛错时整次安装撤销，替代盘的保护领取保持关闭。可再次恢复相同事实。
- `delivery.claim.claim_protected(receiver, call_custody, dispatch_id, request_id)` 在恢复成功后办理名单内领取，成功返回现有的 `ClaimResult`，状态为 `claimed`。保管确认先于外站正式出账与成功返回。已确认的请求同参重投或使用新请求复领都返回同一日报和首执。新请求复领成功也须持久绑定到此日报。
- `audit.custody.Custody(path, destination)` 打开或重开固定外站的独立 SQLite 保管库。实例可按 `call_custody(operation, payload)` 调用，即 `custody(operation, payload)`。`confirm` 载荷为确切事实字典，字段含 `dispatch_id`、`report_id`、`report_revision`、`destination`、`fingerprint`、`material`、`first_request_id` 及有序的 `request_ids`（首项等于 `first_request_id`）；成功返回表示本次保管确认已持久化的同一事实字典。`recovery` 载荷为 `{"destination": ...}`，返回该外站全部确认事实的列表，供整批恢复读取。两库各自提交，外站只经此调用取得保管结果。保管端核对外站、确切外发绑定、日报正文与首执的一致性（含指纹与报告身份），重复办理不能改写已确认的绑定或首执，只能在事实一致时追加新的成功请求绑定；错站、指纹或正文不符、首执变更均抛 `ValueError`。

名单内的普通 `claim` 在配置后须拒绝，且不能占用请求号。名单外照用原接口，两种领取沿用同一外站的请求号归属规则。换绑、错站、材料矛盾、名单外使用保护领取或保护入口尚未开放时抛出 `ValueError`。保管调用或恢复检查点的异常由调用方收到。待确认候选在正式账和首执入口不可见。成功安装后可通过已有 `delivery.ledger.read_posting` 和 `delivery.receipts.read_receipt` 核对外站正式结果。恢复只涉及名单内事实，旧盘中已有的普通日报及首执继续可领。

## 换盘样例

`rollback_lab.py` 提供 `seed_dispatches`、`save_image`、`replace_receiver` 和 `CutCustodyCall`。`D` 是日报 `day` 的第 1 版，读值依次为 7 L、11 L 和一条缺测。`E` 是同日报第 2 版，数值相同，可用于核对确切身份与请求归属。`V1` 可留作普通日报。先接办 E 和 V1，保存早盘，再接办 D 并保存晚盘。本文件的脚本入口运行 V1 对照，展示晚盘上的 D/first 从 R1 改记为 R2。

保护领取 D 时先用 R1，再用 R2 复领。换回早盘并恢复后，D 要保留原版、原序及出处，仍为 18 L，首执为 D/first 且首领为 R1。R2 仍能领 D，改领 E 须拒绝。`CutCustodyCall(call, "before_confirm")` 在首次确认调用送达前中断。`CutCustodyCall(call, "after_confirm")` 在首次确认调用成功后扣留回包。前者换盘后无该笔正式账，后者须凭保管结果恢复实际首执。两件均有确认时，若恢复提交前被 `tests.fixtures.interrupt_at("before_restore_commit")` 中断，替代盘不能查到这一轮的部分安装，重试后两件一起可领。

沿 `.pointwise-runtime.json` 交付核验和演示。清单沿用版本 1 及 cli 类型，setup 是命令 argv 列表，verify 和 demo 分别是一个 argv。保留原完整回归、原示例及其失败退出码。verify 同时核对上述保护链，demo 展示断源换盘后读回的账、R1 首执及 R2 改绑裁决。
