# 本机日常使用与调度

## 已部署的范围

配置 `configs/live.toml`；数据库、原文、控制队列和每轮结果均在 `data/live`。验证股票池 SBUX、HON、SAP 可直接替换为其他已支持的美股代码，或使用 `[universe]` 的 CIK、CSV、SEC 当前名单发现模式。SEC 当前名单不能替代历史退市股票池。

市场、证券池、源、数据类型、日期下限、滚动窗口、请求/下载/任务/耗时/费用上限均在配置中。日常采用近 14 天重叠回看；每 7 天计划一次从配置 start 起的历史修订核对，受同一预算约束。未完成会留下任务，不能把一轮预算耗尽解释为完整历史覆盖。首次安装不会立即强抓多年全部附件；需要历史库时执行下面的 full-history 或 backfill。

实际任务名：`EquityData-0a1f6db1282b-Collector`。任务名由项目和数据目录哈希确定，移动目录后需重新安装。计划时间 **09:00、20:30、23:30，Asia/Shanghai**。Windows 本机时区实际为 `China Standard Time`，每天的 XML 起点带 `+08:00`。当前部署还包含登录触发和每 5 分钟的轻量调度检查；轻量检查只有发现到期时点、待办、到期的有限重试或每周修订任务时才采集。

每日轮次默认：180 个任务、220 次 HTTP 请求（含重试）、150 MB、900 秒、预计请求费用 0 美元。未购买服务；费用字段是可配置的每请求估算，不能代表所有供应商的实际合同费用。单个任务的解析可能在预算检查之间消耗时间，Windows 任务另有终止时限作为外层约束。

## 安装和管理

在项目根目录 PowerShell 执行：

```powershell
.\scripts\manage_local.ps1 -Action Install -Config configs/live.toml
.\scripts\manage_local.ps1 -Action Status -Config configs/live.toml
.\scripts\manage_local.ps1 -Action Run -Config configs/live.toml
.\scripts\manage_local.ps1 -Action Pause -Config configs/live.toml
.\scripts\manage_local.ps1 -Action Resume -Config configs/live.toml
.\scripts\manage_local.ps1 -Action Uninstall -Config configs/live.toml
```

`Install` 固定项目 `.venv\Scripts\pythonw.exe`、工作目录和绝对配置路径。后台进程由任务计划服务启动，不依赖命令终端或 Codex 聊天继续打开。`Run` 立即检查到期与待办；要强制检查指定数据，用 `refresh`。`Pause` 停止领取下一项任务，已经运行的轮次安全完成；`Resume` 恢复并检查补采。卸载只移除通过本项目所有权标识及动作校验的这个任务，保留所有数据、日志和其他 Windows 任务。修改时刻或解释时区后重新 `Install`；`Status` 同时列出配置时刻、已注册时刻和漂移提示，不能仅看配置认为已经生效。

任务使用当前用户的 `InteractiveToken`、普通权限，不保存 Windows 密码。**用户尚未登录或已注销时不会运行**；登录后触发补采。桌面锁定与注销不同，锁定且保持登录允许任务运行。休眠、关机期间不采集，本部署 `WakeToRun=false` 不主动唤醒；恢复后由 Windows 可用调度及轻量检查触发补采，不承诺即时或准点。未实测真实注销、整机重启、睡眠和长假关机，本轮有受控进程终止/错过时点实验；请勿将二者混同。

多个错过的时点合并成一项工作，窗口从最早遗漏时点向前重叠 2 天，并恢复同股票池、来源及数据类型的旧未完成范围。`planning_cursor` 只表示“已经枚举到哪个计划时点”，不表示数据成功。若失败，任务和来源账本分别保留实际状态。全局队列每次取一项，Windows `IgnoreNew` 与内核锁共同阻止并发；手动刷新可在已有采集运行时排队，重复待办合并。请求在采集开始后到达时保留一个后继请求，避免错误地用请求之前的检查满足新请求。

官方行为依据：[StartBoundary 时区偏移](https://learn.microsoft.com/en-us/windows/win32/taskschd/trigger-startboundary)、[InteractiveToken 登录条件](https://learn.microsoft.com/en-us/windows/win32/taskschd/principal-logontype)、[错过计划后的 StartWhenAvailable 行为](https://learn.microsoft.com/en-us/windows/win32/taskschd/tasksettings-startwhenavailable)。其他有夏令时的时区由 Python ZoneInfo 决定到期时点，Windows 生成的固定偏移触发器不等同于完整 IANA 夏令时规则；轻量检查负责补足，本轮实际验证时区只有 Asia/Shanghai。

## 决策前刷新

```powershell
.venv\Scripts\python.exe -m equity_data.local --config configs/live.toml refresh
.venv\Scripts\python.exe -m equity_data.local --config configs/live.toml refresh --symbols BDX,SO --sources sec_edgar --data-types filings,documents,financial_facts --wait-seconds 60
```

省略参数沿用配置。若另一个采集正在执行，命令返回 `pending`，不是完成；返回的 request_id 对应后续结果。`--wait-seconds` 允许在排队时等候，任务即使退出等待仍保存在队列中。默认必要信息清单是 `local_freshness.required_types`；缩小本次查询范围，不会把未检查的必要信息自动判为已满足。阈值按信息类型分别设置，修改配置即可。

每个请求生成：

- `run.log`：启动、PID、失败栈、结束和资源消耗；早期配置错误在项目 `logs/local-startup-errors.log`。
- `engine-run.json`：有效市场/证券/来源/日期/数据类型范围、范围水位、任务结果和预算。
- `decision-report.json/md`：来源检查时间、资料发布/报告期/获取时间、类型新鲜度、内容是否存在、候选/事实状态和未完成事项。
- `new-records.jsonl`：本轮新增/更正版本、原文定位、哈希及来源；没有新记录不等于来源没有新披露。
- `coverage-gaps.json`：失败、未知、拒绝、权限和完整性缺口。

`LastTaskResult=0` 只代表调度器正常返回。必须看报告中的 task_execution_complete、parsing_complete、source_checks_complete、required_freshness_satisfied。独立的 coverage_complete 不会被数据库行数自动设为 true。新鲜度 satisfied 只说明指定来源检查条件满足，必须再看 content_available、期间、核验状态及覆盖缺口。

当前生产库 14 天窗口可能没有新财报；公司历史财报和业务数字应先回填，不能把空库的最新 API 检查当成财务数据已经齐备。RSS 仅覆盖配置的美联储 feed；不表示已掌握股票池的所有新闻。FRED 公开 CSV 为当前快照，不是历史时点版本数据库。

## 回填、重试恢复与重解析

```powershell
# 日常数据目录按配置 start 至今核对，预算未完成会留下旧范围待办。
.venv\Scripts\python.exe -m equity_data.local --config configs/live.toml refresh --sources sec_edgar --full-history

# 固定区间回填；重复相同范围用于幂等检查。
.venv\Scripts\python.exe -m equity_data --config configs/live.toml backfill --sources sec_edgar --start 2024-01-01 --end 2024-04-30
.venv\Scripts\python.exe -m equity_data --config configs/live.toml run --sources sec_edgar --start 2024-01-01 --end 2024-04-30

# 依赖修复后，明确重置指定来源的重试预算，再按正常入口刷新。
.venv\Scripts\python.exe -m equity_data --config configs/live.toml retry-failed --sources official_ir --reason dependency_fixed

# 不下载，重新解析已保存最新原文；旧版本采用独立审计通道。
.venv\Scripts\python.exe -m equity_data --config configs/live.toml reprocess --sources sec_edgar --offline
.venv\Scripts\python.exe -m equity_data --config configs/live.toml reprocess --sources sec_edgar --reprocess-versions all --offline
```

旧 CLI 直接回填与本机调度共享外层锁；已有采集时会明确拒绝直接写入，改用排队 refresh 或等其完成。`run` 默认只恢复其精确配置范围，要恢复别的已保存范围，用 `--resume-scope-ids` 或交给本机调度按股票池匹配。不要删除数据库、锁文件或失败任务来“恢复”。锁所有权由操作系统持有，进程死亡后自动释放；重试预算、失败历史、资源版本另行持久保存。

## 研究 agent 的稳定读取接口

```powershell
.venv\Scripts\python.exe -m equity_data --config configs/live.toml query financial_facts --company-id sec:0000829224 --limit 10
.venv\Scripts\python.exe -m equity_data --config configs/live.toml query news --limit 10
.venv\Scripts\python.exe -m equity_data --config configs/live.toml trace RECORD_ID
.venv\Scripts\python.exe -m equity_data --config configs/live.toml export --out exports/live
.venv\Scripts\python.exe -m equity_data --config configs/live.toml status
```

已补齐的 SBUX/HON/SAP 固定历史验证副本使用 `configs/v4_sec_repeat.toml`（2024-01-01 至 2024-04-30）。例如将上述 `query financial_facts` 命令的配置换为此文件，即可读取本轮已保存的历史字段。它与滚动更新的 `data/live` 独立，未把测试样本库伪装为完整日常历史库。该配置的较大预算只为完整验收，不是每日采集预算。

公司 ID 示例仅用于演示查询参数，不影响采集逻辑。`trace` 返回记录、引用原文及哈希等证据；原文实际文件在 `raw_blobs.path`，相对于该配置 storage。导出保留全部版本，SQL 表视图也包含所有版本，不能直接当作历史可见状态。历史查询使用 `--as-of`（公开可用时间）、`--known-at`（系统实际取得时间）和 `--processed-at`（当时完成解析时间）；不确定发布时间的资料受保守限制。字段合同、原文/候选/事实/统一指标区分详见字段字典和数据合同。

备份恢复见 [操作说明](BACKUP_RESTORE_V4.md)。恢复副本不会自动替换生产目录或计划任务，不会伪造新获取时间；应先核验，再改配置、安装对应任务，最后执行真实来源检查。
