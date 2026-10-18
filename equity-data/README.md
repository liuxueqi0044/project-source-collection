# 通用金融信息获取系统 · V5 竞争证据模块

V5 在原 V4.1 日常研究库上增加竞争主体、业务范围关系、来源入口审核、网页快照、独立分析队列、事件候选、目标关联和证据简报。复用原 Windows 任务与执行锁，不增设服务或付费依赖。

**采集工作流已实现；默认自动语义能力仍有限。** 真实冻结样本的类别提及命中为4/14，完整结构化事实为0/14，尚未达到架构的语义验收门槛。候选不冒充事实，代理审核不冒充人类确认。实际启用范围、失败和交付结果见本轮验收报告。

- [V5 使用与新增股票池/对手操作](docs/COMPETITION_V5.md)
- [V5 字段字典](docs/COMPETITION_FIELDS_V5.md)
- [V5 验收与缺口](reports/v5/ACCEPTANCE_V5.md)
- [真实冻结样本语义结果](reports/v5/semantic_holdout/README.md)
- [配置输入示例](configs/competition-trial.json)

```powershell
.\scripts\competition.ps1 -Action Status
.\scripts\competition.ps1 -Action Refresh -Target sec:0000829224 -Out reports/competition-refresh.json
.\scripts\competition.ps1 -Action Query -Target sec:0000829224 -Out reports/sbux-competition.json
```

## 保留的 V4.1 日常研究库与操作

本轮继续复用现有 Windows 本机服务和 `data/live`，保留原文、版本、失败历史及 V2/V3/V4 验收证据。**SBUX、HON、SAP 仍是用户确认的试运行池**，不是用户的正式投资范围或无偏历史股票池。新增公司通过股票池配置或 CSV 接入，使用同一个历史初始化流程。

日常库的本轮初始化范围固定为 **2024-01-01 至 2026-10-10**，渠道为 SEC EDGAR。采集日期边界与财务报告期间不同：申报可包含更早比较期，实际事实期间须以原文和库存查询为准。初始化按公司、来源和分段日期持久化，预算用尽会留下待办；旧验收库原文只经验证导入与重新枚举解析进入日常库，不复制旧成功状态。

本机运行方案继续使用现有任务：Asia/Shanghai 每日 **09:00、20:30、23:30**，登录检查、每 5 分钟轻量调度检查、遗漏计划合并补采及决策前按需刷新。是否启用、实际完成的范围和最近运行必须检查当前状态，不能用 Windows Ready 代替资料完整性。

- [V4.1 验收、真实日常库存及仍待观察事项](reports/v4_1/ACCEPTANCE_V41.md)
- [日常操作、股票池变更、查询、决策前刷新](docs/LOCAL_OPERATIONS_V41.md)
- [历史初始化、原文复用与时间证据](docs/INITIALIZATION_V41.md)
- [异常恢复窗口与额外采集触发规则](docs/RECOVERY_AND_TRIGGERS_V41.md)
- [V4.1 字段字典：维度、初始化、导入及统一状态](docs/FIELD_DICTIONARY_V41.md)
- [V4.1 项目打包与私人配置排除](docs/PACKAGING_V41.md)
- [备份、验证与恢复](docs/BACKUP_RESTORE_V4.md)
- [既有渠道能力与外部依赖基线](reports/v4/CHANNELS_AND_GAPS_V4.md)

在项目根目录执行：

```powershell
# 查看现有 Windows 任务；不重复安装任务。
.\scripts\manage_local.ps1 -Action Status -Config configs/live.toml

# 以配置的固定范围初始化；已有完成批次会复用。
.venv\Scripts\python.exe -m equity_data.local --config configs/live.toml initialize --max-batches 20

# 决策前刷新当前试运行池。
.venv\Scripts\python.exe -m equity_data.local --config configs/live.toml refresh

# 按公司、资料类型和期间生成真实日常库存与运行状态。
.venv\Scripts\python.exe -m equity_data.local --config configs/live.toml status --out reports/current-live-status

# 获取证据记录；trace 命令可继续追溯原文。
.venv\Scripts\python.exe -m equity_data --config configs/live.toml query financial_facts --limit 20
```

每次结果保存在 `data/live/local-runs/<request_id>/`；统一状态在读取时重新计算当前范围与时效，历史报告中的布尔状态只描述当次运行。**实际库存和本轮完成程度以运行结束后的状态导出与验收报告为准**，本页不预填财务条数，也不把独立验收库的 943 份原文当作日常库已经初始化的证明。

任务执行、资料取得、解析、来源新鲜度、独立覆盖和字段核验分开。深层语义仍分原文、候选片段、已提取事实和统一指标；候选客户供应商、债务、承诺不计为完整事实。完整一天三轮、自然登录补采、休眠恢复及自然新增披露/更正，只在实际观察后记录为真实通过。

新机器需 Python 3.11+，自行重建 `.venv` 并安装依赖；现有机器继续使用已部署环境及本机私配。V4.1 包排除虚拟环境、密钥、私人配置和本机备份副本。以下 V4/V3 内容保留作历史交付记录，不代表本轮最新状态。

## V4 交付记录（历史状态）

本轮在原项目上升级，原 V2/V3 数据、原文、失败证据和参考清单保留。每日股票池是用户确认的 **SBUX、HON、SAP**，仅作验证池；在 `configs/live.toml` 改股票池即可。Windows 已安装任务；具体已观察到的运行、覆盖和限制以 [V4 验收报告](reports/v4/ACCEPTANCE_V4.md) 为准。

- [日常操作、调度及决策前刷新](docs/LOCAL_OPERATIONS_V4.md)
- [调度和数据字段补充字典](docs/FIELD_DICTIONARY_V4.md)
- [资源重试、范围账本与状态](docs/RUNTIME_V4.md)
- [备份恢复](docs/BACKUP_RESTORE_V4.md)
- [渠道、信息覆盖及外部依赖](reports/v4/CHANNELS_AND_GAPS_V4.md)
- [独立单位修复证据](reports/v4/unit-fix.md)

在项目根目录运行：

```powershell
# 当前用户的计划任务状态
.\scripts\manage_local.ps1 -Action Status

# 决策前按股票池、来源和信息类型刷新；不生成交易意见。
.venv\Scripts\python.exe -m equity_data.local --config configs/live.toml refresh --symbols SBUX,HON,SAP --sources sec_edgar,fred,exchange_calendars,public_news --data-types filings,financial_facts,macro_observations,calendar_sessions,news

# 查看各请求状态与最新报告位置
.venv\Scripts\python.exe -m equity_data.local --config configs/live.toml status
```

刷新报告在 `data/live/local-runs/<request_id>/decision-report.json`，最新一份为 `data/live/latest-decision-report.json`。**任务执行、解析、来源新鲜度、原文完整性与字段核验是不同结论。** 当前生产库起步运行采用滚动 14 天；它不是已补齐全部历史的研究库。固定 2024 年历史样本的完整 SEC 回填另存 `data/v4_sec_repeat`。当前源检查成功但没有对应期间资料时，不能视为已经取得所需财报数字。

新机器需 Python 3.11+：`python -m venv .venv`，再 `.venv\Scripts\python.exe -m pip install -e ".[test]"`，配置本机 SEC 联系标识后安装任务。本次实际环境为项目专用 `.venv`；ZIP 不包含虚拟环境、私人配置、密钥和重复本机备份。


## 原 V3 交付记录（历史状态）

V3 保留 V2 数据目录与失败证据，修复负数指引、混合正文、支持文件后到和日期优先级，并增加独立参考覆盖核验、数据合同与恢复验证。真实渠道覆盖以逐渠道报告为准。

最终软件回归 345 项通过；配置范围内 943 个参考资料地址取得 942 个。深层语义抽样仍有 6 个候选、2 个拒绝和 3 个遗漏，完整 SEC 重复回填实验尚未完成。采集结束后另修正了覆盖率计算，因此最终交付代码与初始冻结版本存在一处报告模块差异。验收库保留中止实验的待办；正式股票池应在配置中指定新的 `storage` 目录。

- [V3 验收与未完成事项](reports/v3/ACCEPTANCE_V3.md)
- [独立覆盖分母与评分](docs/COVERAGE_V3.md)
- [候选、事实与统一指标的数据合同](docs/DATA_CONTRACTS_V3.md)
- [V3 字段与时间查询补充字典](docs/FIELD_DICTIONARY_V3.md)
- [持续运行、日期与历史重解析](docs/RUNTIME_V3.md)
- [支持文件依赖与自动重新解析](docs/SEC_DEPENDENCIES_V3.md)
- [授权渠道接入依赖](docs/DEPENDENCIES_V3.md)
- [公共渠道真实运行与未解决缺陷](reports/v3/public_channels/PUBLIC_CHANNELS_V3.md)

```powershell
# YOUR_SYMBOLS 替换为同一个逗号分隔股票池；长期使用可写入配置。
python -m equity_data --config configs/machine.toml backfill --symbols YOUR_SYMBOLS
python -m equity_data --config configs/machine.toml run
python -m equity_data --config configs/machine.toml update --symbols YOUR_SYMBOLS
python -m equity_data --config configs/machine.toml watch --symbols YOUR_SYMBOLS --interval 3600

# 旧原文版本重算进入独立审计表；最新版本仍按常规追加解析结果。
python -m equity_data --config configs/machine.toml reprocess --reprocess-versions all --offline
python -m equity_data --config configs/machine.toml reprocess-audits --limit 5

# 公开状态、原文取得状态、已解析系统状态分开限制。
python -m equity_data --config configs/machine.toml query financial_facts --as-of 2024-04-30 --known-at 2026-10-09 --processed-at 2026-10-09

# 参考清单由独立原文读取或人工审核建立，不得由入库结果反推期望。
python -m equity_data --config configs/v3_holdout.toml coverage --reference reports/v3/holdout-reference-final.json --out reports/v3/holdout-coverage-corrected
```

`known_at` 单独使用只限制原文证据取得时点；重现当时已经形成的结构化字段，还须提供 `processed_at`。表格解析仅支持合同中列出的结构；拒绝、候选和未知均不计已核验事实。以下原有命令保持兼容。

输入证券池、日期和数据类型，系统发现公司身份，枚举资料，生成持久任务，下载原文，解析字段，保存版本并报告未完成工作。Python + SQLite 单机运行，原始文件按 SHA-256 留存，不依赖分布式服务或 LLM。

**主要可用通路是美股 SEC 申报、附件及结构化财务。** 官方 IR 支持有来源证据的入口和静态 HTML/RSS/PDF；宏观、政策、新闻和行业指标分别配置。实际接通、下载失败、解析限制及待授权情况见 [V3 验收报告](reports/v3/ACCEPTANCE_V3.md)；[V2 报告](reports/ACCEPTANCE_V2.md)作为历史证据保留。测试样本不是无偏历史股票池。

## 安装与新股票池

在本项目目录内使用 Python 3.11 或以上：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install -e . --no-deps
.\.venv\Scripts\Activate.ps1

# 使用真实联系身份；本机已有 machine.local.toml 会自动读取。
$env:SEC_USER_AGENT = 'YourResearchProject your-real-contact@your-domain.com'

.\.venv\Scripts\python.exe -m equity_data --config configs/machine.toml backfill --symbols IBM,COST --sources sec_edgar,official_ir --start 2024-01-01 --end 2024-03-15
```

默认配置没有固定公司。长期使用时，在 `[universe] symbols` 中填写股票池，或配置 `csv_file`（相对配置文件目录）。CSV 支持 `symbol,cik,security_id,official_ir,website`。只有股票代码也能通过 SEC 目录发现 CIK；退市申报人可提供 CIK。当前目录不能证明历史上市身份或股类连续性，CIK 也不是证券股类主键。

命令行的股票池、来源和日期覆盖只对当次运行有效，不会写回配置。`run` 可继续已有持久任务；以后 `update` 或 `watch` 重新发现股票池时，须再次传入 `--symbols` 或预先将股票池保存到配置。下方省略 `--symbols` 的更新示例假定已配置股票池。

公司名字、财务答案或公告网址不需要写入生产代码。`IBM/COST` 是开发样本。初轮 `DE/ADBE/JPM` 暴露了表格指引单位错误，失败及修复记录均保留；后续使用新证券池验证，具体冻结批次见验收报告，不能把参与过修复的公司再称作未调试样本。

SEC 邮箱只用于 User-Agent。Alpaca/FRED 密钥从环境变量读取，任务与URL证据不保存密钥。压缩包排除 `*.local.toml`；本机现有工程可继续使用已保存联系配置。

## 发现、续跑、持续更新

```powershell
# 发现根任务；继续读取列表后才知道完整的可发现清单。
python -m equity_data --config configs/machine.toml discover
python -m equity_data --config configs/machine.toml run --max-tasks 100

# 当前日期及回看窗口的新披露；全历史重扫检查旧资料更改。
python -m equity_data --config configs/machine.toml update
python -m equity_data --config configs/machine.toml update --full-history

# 仅重解析已保存原文，不下载缺失文件。
python -m equity_data --config configs/machine.toml reprocess --offline --sources sec_edgar

# 前台持续运行；Ctrl-C 停止，队列保留。每24轮重扫历史（可配置）。
python -m equity_data --config configs/machine.toml watch --interval 3600
# 有界启动检查：
python -m equity_data --config configs/machine.toml watch --interval 5 --cycles 2
```

`run_daily.ps1` 提供单轮更新及报告入口，可交给 Windows 任务计划程序。交付未注册开机任务或留下无限后台进程。

SEC→IR 依赖发现会在上游证据入库后自动唤醒。无法发现官网、JS页面无静态列表、只有录音没有文字稿等会明确报告。已有可靠入口可通过配置补充，尚未支持的网站可添加适配器。

预算每轮独立：任务数、请求数、字节、时长、估计费用。HTTP 重试计入请求量；字节上限可能被最后一个读取块略微超过，但残片不会当原文。费用仅按显式单次请求价格估算，订阅/授权费用另列依赖。预算触发后任务保持 pending/deferred；池的 `limit/offset` 也保留遗漏名单及续跑位置。截断不能算完整覆盖。

## 查询、证据、导出

下列命令以保留的 V2 样本目录示范兼容用法。查询本轮 V3 留出样本时，将配置改为 `configs/v3_holdout.toml`，并使用该库实际保存的公司 ID 与记录 ID。

```powershell
python -m equity_data --config configs/holdout.toml query financial_facts --limit 5
python -m equity_data --config configs/holdout.toml query financial_facts --as-of 2024-02-15T21:00:00Z --known-at 2026-10-09T23:59:59Z
python -m equity_data --config configs/holdout.toml query guidance --all-versions
python -m equity_data --config configs/holdout.toml trace 'rec:REPLACE_WITH_RECORD_ID'
python -m equity_data --config configs/holdout.toml financial-snapshot --company-id sec:0000315189 --as-of 2024-03-01
python -m equity_data --config configs/holdout.toml export --out exports/holdout
python -m equity_data --config configs/holdout.toml status
python -m equity_data --config configs/holdout.toml sources
python -m equity_data --config configs/holdout.toml report
```

`--as-of` 约束公开可用时间；`--known-at` 约束系统实际获取时间。日期截止 UTC 当日末，盘中查询须带时区。今天下载旧申报不会伪造过去已获取。SEC 文件日采用次日纽约午夜的保守边界，不宣称精确盘中传播时间；当前 IR 网页、RSS、FRED CSV 版本只能从本次观察起可靠使用。更晚时间证据会限制旧的过早假设；跨文件数字也要求必要 context/unit 原件已获取。

`trace` 返回原文、哈希、获取记录、定位及证据。数字、期间、单位、容差和解析版本分别保存。当前原文无法重新产生旧字段时记录字段不可用，不能静默沿用旧数字。`financial-snapshot` 仅比较标准 API 概念的同单位/精确期间；自定义和维度事实单独查询，不擅自合并。

通用表 `records.data_json` 保留来源特有字段，同名 SQL 视图便于筛选。导出包含所有版本、证据和任务表，数据集也导出为 JSONL。原价和 `adjusted_bars` 分开；`adjust` 沿用可重算方法，可靠公司行为、币种或时点缺失时拒绝不可靠计算。

## 渠道能力边界

经核验否定的摘录保留在 `records` 及 `record_validations`，普通查询会排除；`query --all-versions --include-rejected` 可审计原错误和原因。V3 支持明确期间、单位及行列对应的有限矩形指引表格；合并单元格、范围不明、歧义脚注等结构仍拒绝，具体边界见 [数据合同](docs/DATA_CONTRACTS_V3.md)。段落指引也按严格规则处理。

| 通道 | 接口能力 | 本次须明确的限制 |
| --- | --- | --- |
| SEC 目录/EDGAR | 当前池发现、历史分页、主文、EX-*、Companyfacts、iXBRL | 当前名单非历史主档；复杂变换、taxonomy和扫描件可缺失 |
| 官方 IR | 公司披露链接、静态分页、RSS、HTML/PDF | JS、403、删帖和文字稿权限具体列缺口 |
| exchange_calendars | 独立市场/交易所日历时段 | 当前库规则不证明历史停牌或紧急休市公告 |
| FRED/ALFRED | 无key CSV；有key元数据和vintage分页 | 真实验证是当前CSV；API修订接口尚待key验证 |
| Federal Register | 主题/机构分页、详情、正文任务 | 预算未完成任务保留；法律PDF和网页研究版本区分 |
| RSS/Atom | 指定来源、原始条目、更新及重复候选 | 摘录不是全文，不代表完整历史新闻库 |
| BLS | 配置序列近期公开GET | 本次真实403，不能宣称接通 |
| Alpaca | 有凭据后的原价/公司行为分页 | 本次无密钥，仅模拟测试；IEX不等同全市场 |
| 商业预期/主档/研究 | 依赖边界与授权人工导入 | 没有历史一致预期，无法研究相对市场预期的业绩差异 |
| A股 | 独立市场配置和日历 | 公告、行情和状态适配器尚未实现 |

V3 真实运行还发现未解决的调度问题：BLS 持续 403 时，每轮更新会重试历史失败 generation 并新增失败任务，请求数在三轮中由 1 增至 2、3。当前仅证明其他来源可继续执行，未证明 BLS 恢复或长期失败任务收敛；详见 [公共渠道报告](reports/v3/public_channels/PUBLIC_CHANNELS_V3.md)。

支持分部/产品/地区 XBRL 维度、自定义标签及同一申报跨文件 context/unit。指引只在原文同一证据范围内可验证数值、单位、期间时输出；不支持的语义拒绝输出。客户、供应商、承诺、债务及风险叙述是有出处的待核验候选，不能当已结构化数字或已确认关系。未披露内容不补造。

复用 requests、exchange_calendars、lxml、BeautifulSoup、pypdf 和 SQLite。Arelle/OpenBB/edgartools 等候选的取舍及授权依赖见 [来源说明](docs/SOURCES_V2.md)。完整 [来源注册表](configs/source_registry.json)、[适配说明](docs/ADAPTERS.md)、[字段字典](docs/FIELD_DICTIONARY_V2.md) 和 [SEC/IR补充](docs/SEC_IR_ADAPTER_NOTES.md) 随代码交付。

## 人工材料与验收

```powershell
python -m equity_data --config configs/machine.toml import-jsonl path/to/authorized-materials.jsonl
python -m pytest -q
python scripts/audit_machine.py --storage data/holdout --out reports/holdout-audit
```

人工入口要求来源、权限、信息性质；调查还需样本和方法。保密、泄露、未授权材料拒绝导入。人工内容不计自动成功率。格式见适配说明。

验收分别报告软件行为、实际字段、资料完整性、未调试证券泛化。V3 冻结哈希在 `reports/v3/production-freeze.json`，新留出样本资料和库在 `data/v3_holdout/`，公共渠道资料在 `data/v3_public_channels/`；待办和拒绝项也是结果。V2 原目录及失败证据继续保留，不能重复计为 V3 未调试样本。测试通过不意味着每条数据已核验。预填数字已移出生产配置并被自动入口拒绝，见 [迁移说明](docs/V1_MIGRATION.md)。
