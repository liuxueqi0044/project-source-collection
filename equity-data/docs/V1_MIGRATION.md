# V1 到通用采集机的迁移

V2 默认入口是 `configs/machine.toml`，不再以 AAPL、NVDA、META、TWTR 的公司名单、公告地址或预填数字驱动采集。证券池在 TOML、CSV 或 `--symbols` 中配置；新证券通过目录发现公司 CIK，申报正文和附件由 SEC 清单枚举。

原 `data/sample/` 数据库、原始文件和版本均保留。它是历史交付的审计快照，不计入 V2 自动采集、泛化或字段准确性结果。原先预填公司数字的 manifest 已移到 `data/legacy_manifest/public_samples.rejected.json`。自动入口拒绝其中 `records.data`；原先仅凭关键词检查的指引、公司行为和事件记录不能视为经过 V2 字段核验。原始资料仍可由新解析器重新处理；明确授权的人工摘录也可经单独 JSONL 入口导入，标记为人工，不能混进自动成功率。

`configs/us.sample.toml`、`us.local.toml`、旧报告和旧验收脚本只为重现 V1 保留。CLI 的 `backfill/update` 已统一走通用引擎，旧的 `sec/public/research` 源名称请改为注册表中的来源 ID。V1 Python 批量函数保留兼容测试，不能代表 V2 任务清单接口。

数据库采用增量建表，没有覆盖原始资料或删除旧版本。V2 新数据库分别在 `data/development/`、`data/holdout/` 和 `data/public_channels/`，测试样本与默认实际运行目录 `data/machine/` 分开。当前证券名单仍不是无偏历史股票池。
