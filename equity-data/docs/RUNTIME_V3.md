# V3 执行、日期与历史重解析

## 日期优先级

执行边界复制证券配置后，为所有适配器统一有效日期，优先级为：

1. 命令行显式 `--start` / `--end`（分别覆盖对应边界）。
2. `update` / `watch` 当轮计算出的日期；`--full-history` 使用全局历史起点。
3. 普通 `backfill` 的证券级日期默认值。
4. 全局配置日期。

命令行仅覆盖一个边界时，另一个边界仍遵循上述规则；组合日期非法会报错，不能偷偷修正。传给适配器的 company scope、security 字典和 `config.securities` 使用相同有效日期。原始配置和身份缓存不被改写。

`reports/v3/runtime-before.json` 保存 V2 日期错误和循环中断的原始复现，包括当时生产文件哈希。`runtime-after.json` 使用同一复现程序验证修复。测试证券完全虚构，证据不代表真实渠道覆盖。

## 异常与恢复

证券目录临时网络异常会记录缺口，允许独立全局来源继续。只有同一股票池配置的已有缓存身份可以继续使用，明确标记陈旧及原解析时间；新的未知代码不得套用其他股票池缓存。

`watch` 在循环边界记录失败并退避，默认最多连续失败 5 轮，初始等待 5 秒、上限 300 秒。`scheduler.max_consecutive_cycle_failures`、`cycle_retry_seconds`、`cycle_retry_max_seconds` 可配置。配置、文件权限或缺文件错误直接失败，不无限重试。

每轮结果追加到数据目录 `watch_events.jsonl`；历史失败记录不删除。一次执行完成、解析完成与资料覆盖完成是不同字段：`task_execution_complete`、`parsing_complete`、`coverage_complete`。成功完成所有任务并不证明未发现的资料不存在，也不证明字段没有遗漏。

采集执行使用数据目录 `.machine-worker.lock` 的操作系统文件锁，支持**单机单采集执行者**。长下载或解析期间，即使数据库租约过期，第二个采集执行者仍不能进入。进程结束后操作系统自动释放锁；下次运行恢复持久任务。低层队列更新另有拥有者检查，已被恢复的旧执行者无法完成或覆盖新任务。锁文件存在本身不代表锁被占用，不需要人工删文件。

当前交付并未安装常驻服务，也没有把模拟测试等同于数日实盘持续运行。

真实公共渠道验证发现一项仍未解决的长期运行问题：BLS 持续 HTTP 403 时，历史失败 generation 被逐轮重试，同时产生新任务，请求数在三轮中为 1、2、3。循环级异常退避不能解决这种任务级失败累积。当前证据只证明其他渠道继续运行，未证明 BLS 恢复或失败队列收敛；见 [真实运行报告](../reports/v3/public_channels/PUBLIC_CHANNELS_V3.md)。

## 历史原文重解析

```powershell
python -m equity_data --config configs/machine.toml reprocess --reprocess-versions latest --offline
python -m equity_data --config configs/machine.toml reprocess --reprocess-versions all --offline
python -m equity_data --config configs/machine.toml reprocess --reprocess-versions all --raw-ids "sha256:REPLACE_WITH_64_HEX_DIGEST" --offline
```

`latest` 重新解析各资料当前保存版本。`all` 遍历所选来源、公司、数据类型的所有已保存获取版本；可以用 `--raw-ids` 选择内容版本。日期参数控制发现的日期范围，不作为任意历史原文的日期过滤器，因为很多原文缺少可靠日期。需要精确选历史原文时使用来源、公司、数据类型及原文 ID。

最新版本按常规写入 append-only 解析结果。旧版本的重解析运行在隔离 SQLite 副本中：禁止网络和写入新原文，产生的事实、撤销、验证及时间约束不会修改主库研究状态。重建结果写入 `machine_reprocess_audits`，明确标注“历史解析重建审计，不是当前事实，也不是系统当时已知信息”。这样重放旧网页不会把旧数字重新变成当前最新数字。

审计表保留原文 ID、精确获取版本、当次运行、解析时间、解析器版本、结果及字段。`reprocess-audits --raw-id "sha256:REPLACE_WITH_64_HEX_DIGEST"` 或 `--resource-id "resource:REPLACE_WITH_RESOURCE_DIGEST"` 查询审计；替换为数据库或 `trace` 输出中的完整 ID。普通 `export` 会导出该表。重复相同输入和解析结果不会新增重复审计。若要把历史审计结果用于后续研究，必须另行定义与验证提升规则；本轮不自动提升。

历史查询的三个时钟分别为：`--as-of` 限制公开可用时间；`--known-at` 保留 V2 的原始证据获取截止语义；`--processed-at` 增加结构化结果创建截止及其证据、核验的截止。要重建系统在某时点实际已拥有的结构化结果，应同时指定相同时间的 `--known-at` 和 `--processed-at`。仅指定 `--known-at` 仍允许用今天的解析器重建当时已取得的原文，不等于当时已经解析出了这些字段。

V3 开始在获取检查点保存每个原文版本的资料元数据快照。V2 已有历史原文通常只有当前资料元数据；此类审计明确记录 `resource_metadata_saved_with_version=false`，不能借当前元数据重建过去的公开时间。

全部版本重解析需要对数据库创建隔离副本，适合单机和受预算限制的批处理，不是为海量并行重算优化的实现。执行仍受任务数、时间预算控制；未完成任务保留，可通过 `run` 续跑。

## 证据范围

`tests/test_v3_runtime.py` 覆盖日期优先级、缓存身份边界、来源隔离、循环退避、配置错误、单实例、过期执行者、原文版本重放、幂等及关闭数据库后恢复。还包含子进程在原文检查点后直接 `os._exit(7)` 的实际本地崩溃及零下载恢复测试。它们是模拟或本地持久状态测试。真实请求、实际新增披露和真实源更正证据必须在渠道验收中另外报告。
