# V4.1 历史时间查询用法与边界

`--known-at` 表示“原文证据在截止时刻已经取得”，**不表示字段当时已经解析完成**。重建历史机器中的结构化记录，应同时指定 `--as-of`、`--known-at`、`--processed-at`，并阅读下述尚未解决的时间约束限制。没有 `--as-known-at` 参数；Python 参数名是 `known_at`。

## 三种时间口径

| 参数 | 实际限制 | 不能据此推断 |
|---|---|---|
| `--as-of` / `as_of` | 根据 `available_at`、修订歧义、当前副本规则及时间下界约束，过滤公开可用时间；公开时间未知的记录排除 | 系统当时已经取得或解析。它可以表示今天读取保存的旧原文后，对过去公共信息进行重建 |
| `--known-at` / `known_at` | 过滤记录的 `retrieved_at`，并检查必要支持原文是否已经取得；限制所采用的核验决定和已知时间约束 | 当时已有结构化字段；今天首次解析、重解析旧原文的记录仍可能返回 |
| `--processed-at` / `processed_at` | 额外要求 `records.created_at` 不晚于截止时刻；同时把原文获取截止收紧至不晚于该时刻 | 整个历史机器的任务、配置及所有后续解释状态均已重现；时间约束的解释时间仍有下文所列缺口 |

`known_at` 与 `processed_at` 同时给出时，原文获取、支持原文及相关决策的截止使用两者中较早者。`processed_at` 单独给出也会限制原文获取，但不替代 `as_of` 的公开可用约束。日期型参数如 `2026-10-09` 表示 **UTC 当日结束**；北京时间和盘中查询应明确写偏移量，如 `2026-10-09T23:59:59+08:00`。

原文导入应保留真实历史 `retrieved_at`；今天首次产生的结构化记录，其 `created_at` 是今天。二者分离是必要的时间证据，并不等于系统昨天已有该结构化数字。

## 可使用的入口

在项目目录使用固定 Python 环境。下例的公司 ID 仅为试运行池中已有公司的查询过滤条件，不是生产采集规则。

今天基于已取得档案重建某个公共信息时点：

```powershell
.venv\Scripts\python.exe -m equity_data --config configs/live.toml query financial_facts --company-id sec:0000829224 --as-of "2026-10-09T23:59:59+08:00" --limit 10
```

同时限制当时取得的原文和当时已产生的解析记录：

```powershell
.venv\Scripts\python.exe -m equity_data --config configs/live.toml query financial_facts --company-id sec:0000829224 --as-of "2026-10-09T23:59:59+08:00" --known-at "2026-10-09T23:59:59+08:00" --processed-at "2026-10-09T23:59:59+08:00" --limit 10
```

这两个命令均不替代覆盖率、来源新鲜度和字段质量检查。空结果可能表示当时尚未取得、尚未解析、公开时间不足或被拒绝；不能一律解释为当时来源未披露。结果的 `system_time_semantics`、`point_in_time_status`、`effective_available_at`、`created_at`、`retrieved_at`、质量和证据关系必须一起传递给研究 agent。

## 已证实的查询差异

合成原文于 2026-10-09 取得，其公开边界早于该日；事实于 2026-10-10 首次解析。查询截止为 2026-10-09 UTC 当日末：

| 查询 | 返回该事实 | 正确解释 |
|---|---:|---|
| `as_of` | 是 | 今天可以从当时已公开的原文重建事实 |
| `as_of + known_at` | 是 | 当时已取得原文；不能据此说字段当时已解析 |
| `as_of + known_at + processed_at` | 否 | 首次解析发生在截止之后，记录被排除 |
| `financial-snapshot` 的 `as_of + known_at` | 是 | 此快捷接口只有公开和原文获取口径，不是历史已解析机器快照 |

该演示仅使用独立临时数据库，未打开 live 数据库、未修改生产代码。证据见 [pit-query-demonstration.json](../reports/v4_1/pit-query-demonstration.json)。现有 `test_processed_time`、`test_time_constraints`、`test_validation_decisions`、`test_store` 共 23 项相关测试通过，日志见 [pit-existing-tests.txt](../reports/v4_1/pit-existing-tests.txt)；软件测试不是自然运行或真实来源时点准确性证明。

## 尚未解决的接口与时间边界

1. `financial-snapshot` CLI 和 `financial_snapshot()` 当前没有 `processed_at` 参数。它适合带上述限制的标准概念公共信息重建，**不推荐用于严格历史机器状态**。严格结构化记录过滤请使用前面的 `query` 入口；不能将两个接口描述为时间语义相同。
2. 时间下界表 `availability_constraints.learned_at` 目前采用支持原文的 `retrieved_at`，没有独立保存该约束实际被解析/创建的时间。若某份旧原文今天才被解释出更晚的公开下界，这个约束可能影响昨天的 `processed_at` 查询。合成演示中，同样的三参数历史查询在加入今天解释的约束前返回 1 条、之后返回 0 条。这是**保守排除造成的历史状态变化**，不是把未来数字加入历史；但不能宣称完整重现当时机器给出的所有答案。对应代码为 `Store.restrict_availability()` 与 `Store.query()` 的约束过滤。
3. `processed_at` 能证明结构化记录行在截止前已存在，不能证明当时策略、配置、模型或研究人员已经使用该记录；也不能补足来源发布时间、历史网页版本及独立传播时间的证据。
4. 普通 SQL 数据集视图和全量导出保留所有版本，不自动应用这三个过滤条件。直接取最新版本不等价于历史查询。

本轮保留上述已明确的合同，不偷偷改变旧查询行为。第 2 项是合成输入确认的剩余边界，未声称试运行公司的真实历史记录已发生同类问题。
