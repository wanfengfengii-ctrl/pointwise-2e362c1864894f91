# 流域资料中心固定日报交付仓

站务员提交监测站的升数和缺测出处。白班核账员选择确切的站批修订冻结日报。夜班先登记外发，再将固定日报交给指定外站。外站值班员领取正式账，或者源仓值班员撤发。次班审计员按原外发号查账，核对原首执、原行出处和每个站点的统计。

## 运行

使用 Python 3.11 标准库。从项目根目录运行 `python3 -B check_project.py` 执行完整回归。该入口由控制台提供。直接运行套件可用 `python3 -B -m unittest discover -s tests -t .`。运行 `python3 -B demo.py` 在临时目录完成一轮交班，退出时清理样例库。原日报为 7、11、0 L 加一条缺测，合计 18 L。后续更正得到 20 L，旧外发号和原首执仍对应 18 L。

## Python 接口

源仓、外站与复核各使用独立的 `sqlite3.Connection`。每个新打开的连接分别调用 `dispatch.catalog.initialize_source(source)`、`delivery.register.initialize_delivery(receiver, destination)` 或 `audit.record.initialize_reviews(reviews)`。初始化可重复调用并保留已提交业务。连接使用 SQLite 默认的事务和行读取设置，调用写接口前保持连接空闲。传入 `None` 表示源仓暂时无法访问外站，独立领取始终只需外站连接。各库路径由调用者选择。

| 入口 | 参数与返回 |
| --- | --- |
| `intake.batches.record(source, batch_id, station_id, rows)` | 接纳整批，返回 `BatchRevision`。首次使用的合法站标识随初始站批登记。已存在的批号通过更正入口追加修订。 |
| `intake.corrections.correct(source, parent, rows)` | 用更正后的完整行列表追加修订，`parent` 为 `(batch_id, revision)`。可引用任一实际父修订。 |
| `daily.freeze.freeze(source, report_id, selections)` | 将有序批修订对冻结为 `ReportBody`。`read_frozen(source, report.ref)` 按确切成果复读。 |
| `dispatch.catalog.dispatch(source, dispatch_id, report_ref, destination)` | 固定外发号、日报修订及唯一外站，返回 `DispatchBinding`。`read_dispatch_material(source, dispatch_id)` 返回原绑定及固定材料文本。 |
| `delivery.admit.admit(receiver, binding, material)` | 校验并保存实收正文，返回 `DeliveryEntry`。材料待办时仍无领取资格。调用 `dispatch.resume.resume_handoff(source, receiver, dispatch_id)` 可完成或续办接办，也可用其 `material=` 投递实际收到的正文。 |
| `delivery.claim.claim(receiver, dispatch_id, request_id)` | 返回 `ClaimResult`，状态为 `pending`、`withdrawn` 或 `claimed`。签收结果包含固定 `report` 及原 `receipt`。复领允许使用原请求号或同一外发号下的新领取请求号。 |
| `dispatch.withdraw.withdraw(source, receiver, dispatch_id, request_id)` | 返回 `pending`、`withdrawn` 或 `claimed`。外站不可用时可用 `None`，已经确认的终局仍可重放。 |
| `audit.history.review(source, receiver, reviews, dispatch_id)` | 保存并返回 `ReviewRecord`。`audit.record.read_reviews(reviews, dispatch_id)` 按记录顺序复读历史。 |

观测采用 `{"liters": 7, "source": "北站纸簿第 1 行"}` 或 `{"missing": "探头停测", "source": "北站值班记录第 3 行"}`。另一字段可省略或为 `None`。升数用 Python 整数精确累计，布尔值无效。原行位置从 1 起，日报中的选批次序从 0 起。重复选择同一批修订时，各次选择按列表位置分别计量。分站按有明细的站点首次出现次序排列。空日没有明细或分站小计。

非法输入、材料不符及同号异参抛出 `ValueError`，未找到的确切修订或源仓外发号抛出 `KeyError`。领取与撤发各有独立的请求号命名空间。有效请求处于待续时也保留原参数。存储错误由 SQLite 报告，恢复后可重投相同请求。两库之间由资料中心的可信调用方传递接办结果和处置消息，每个外站库初始化时固定自身站标识。

接办成功前，待办来件不能入账。尚未移交处置权时，可在外站停机期间由源仓撤发。接办成功后，外站无需回查源仓即可正式领取。先撤发的原号无正式账和首执，先签收的原号保留固定正文与原首执。提交后失答，可重开相应库按原号继续办理。复核用实际入账明细重算账目，逐行从确切父修订验证出处，并保留每次核对时看到的状态。
