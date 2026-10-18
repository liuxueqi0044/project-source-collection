# V5 阶段 A：竞争信息获取与证据工作流

本模块复用 V4.1 的 Python、SQLite、原文库、任务队列和 Windows 单执行者锁。没有第二套常驻服务、云服务器、付费 API 或自动交易功能。目标池与竞争者池分开；品牌、私人公司和境外主体可使用稳定 entity_id，不要求证券代码。试运行样本不是用户投资范围。

## 能力边界

采集与分析分别验收：公共静态网页、RSS/Atom、受限目录枚举可归档并持续核对；分析层输出候选、提取事实、范围关联和研究推断，状态不能互相替代。默认规则的真实保留样本表现见 `reports/v5/semantic_holdout/README.md`，未达到架构中 90% 精度/80% 召回目标，不能称为已完成自动竞争研究。

已有来源适配器、身份与关系库、严格证据校验、版本化端点、可恢复分析任务、配置范围冻结和只读 JSON/Markdown 查询。网页变化不等于业务变化，首次价格只建立基线。仅有明确业务竞争、但地区或客户未知的主体进入每日一次的观察池，并保留缺口；不能生成全公司竞争结论。未确认关系不进入正式竞争者抓取池。

## 命令

以下命令在项目根目录执行，使用现有 `.venv\Scripts\python.exe`；`--out` 为顶层参数，放在子命令前。Windows直接运行Python时加 `-X utf8`，避免含商标符号等原文的JSON在系统默认GBK输出中失败；`scripts/competition.ps1` 已自动设置并接受大小写Action。

```powershell
# 查看日常竞争库及队列，不访问网络、不写库
.\.venv\Scripts\python.exe -X utf8 -m equity_data.competition --config configs/live.toml status
# 查看按证据关系生成的有界监测计划
.\.venv\Scripts\python.exe -X utf8 -m equity_data.competition --config configs/live.toml plan
# 导入主体、关系和来源审核资料；失败会保留在输出中
.\.venv\Scripts\python.exe -X utf8 -m equity_data.competition --config configs/live.toml --out reports/operator-import.json import configs/my-competition-pool.json
# 决策前仅刷新指定目标及其已确认对手；与正常采集共用锁
.\.venv\Scripts\python.exe -X utf8 -m equity_data.competition --config configs/live.toml --out reports/competition-refresh.json refresh --targets sec:0000829224
# 强制检查已配置入口（仍受原任务失败门控和预算约束）
.\.venv\Scripts\python.exe -X utf8 -m equity_data.competition --config configs/live.toml refresh --force
# 只处理已保存原文，不发起网页请求
.\.venv\Scripts\python.exe -X utf8 -m equity_data.competition --config configs/live.toml process
# 只读研究证据包，自动同名生成Markdown简报
.\.venv\Scripts\python.exe -X utf8 -m equity_data.competition --config configs/live.toml --out reports/sbux-competition.json query sec:0000829224
# 历史查询：公开、获取、处理时间分别限制
.\.venv\Scripts\python.exe -X utf8 -m equity_data.competition --config configs/live.toml query sec:0000829224 --as-of 2026-10-10T12:00:00Z --known-at 2026-10-10T12:00:00Z --processed-at 2026-10-10T12:00:00Z
# 用保存的公司披露发现候选；同现和同业均不自动确认
.\.venv\Scripts\python.exe -X utf8 -m equity_data.competition --config configs/live.toml --out reports/candidates.json discover --targets sec:0000773840
```

`refresh` 创建持久化请求；若执行者忙，返回排队状态，由原任务计划器后续领取。`process` 不代表语义完整。`discover` 默认最多检查最近100份已保存文档，返回未检查数量；它只识别已登记主体的名称/别名，不具备全网发现和未知实体消歧能力。

## 配置和新增公司

`configs/competition-trial.json` 为可编辑关系配置格式；公司名、稳定ID、入口URL、审核摘录是输入数据，不是生产规则。`sources` 定义公开证据URL，`evidence_refs.source_key` 关联该响应，导入时必须核验摘录、定位、内容哈希及获取记录。任何不匹配都会拒绝对应记录。原文中的数值不应填入此文件充当自动提取答案。

新增公司步骤：登记稳定主体及其公开身份证据；配置或发现关系候选；审核业务、地区、客户范围；登记官方或明确第三方入口；将目标ID加入 `[competition].target_ids`；执行 import 与 refresh。新增公司无需修改生产代码。新网站的动态渲染、未知表格或特殊登录若不受支持，会保留缺口；这不等同于任何网站都能自动接通。

只有证据明确的语义才可统一。关系审核 `reviewer=agent_evidence_review` 代表代理依据原文整理，不能计为人类确认或独立事实核验。关系有效开始时间不清楚时保持未知，不回填成报告期末。

## 调度和预算

沿用唯一 Windows 任务及 Asia/Shanghai 每日 09:00、20:30、23:30。竞争模块在普通增量后运行，使用本轮剩余请求、字节、时间预算与自身预算的较小值。历史财报初始化批次不自动额外刷新竞争者。

核心关系按三次正常时点检查；观察关系成功后24小时才再次到期，实际由下一正常时点领取。5分钟调度脉冲仅检查既有计划、未完成分析与有证据的到期续跑，不无条件刷新。采集预算中止保留原 scope/cycle，从旧任务继续，不能每轮重新发现；失败按退避和上限处理，达到上限等待配置修复/明确重置。解析失败使用独立分析队列，最多3次、15分钟起指数退避；不会因此重下已取得原文。

配置参数：`max_depth=1`、每目标5核心/10观察、全局30独立对手；`competition.budget` 控制请求/字节/时间/任务；`max_analysis_jobs`、`max_analysis_seconds` 控制本地解析。超过上限的关系、链接或任务明确保留排除/未完成状态。修改范围会产生新的范围指纹；旧请求继续使用其冻结的主体、关系和入口版本。

本机使用特殊DNS路由时可显式设 `[competition.transport] dns_mode="cloudflare_doh"`。该方式用固定公共IP连接受信解析服务，再将目标HTTP连接固定到已验证公共IP；保留真实域名TLS验证。解析请求与重定向都计入预算并保留原始响应。默认系统DNS模式仍拒绝私网、回环、链路本地地址和fake-IP。403、验证码及授权限制不会被绕过。

## 查询与时间

研究 agent 可使用 query 和 trace，获取原文路径、hash、定位摘录、主体、关系/事件版本、取得时间及缺口。当前网页只证明今天保存的版本；页面自己写的发布日期不能证明过去文本相同。派生关系/事件默认最早可见时间为本次处理，三种时间过滤递归检查支撑证据及确切关系版本；无法重建时会排除并报告。

事件候选、来源已陈述事实、已确认范围关联、研究假说分别保存。简报只有在事件事实、业务/地区/客户匹配和有效期均成立时进入核心关联，不输出利润影响。默认没有“统一指标”或“投资结论”自动完成的声明。

## 备份和回滚

按 `docs/LOCAL_OPERATIONS_V41.md` 的统一备份命令执行，它包含所有新增 records/raw 和新增运行表；不可只复制 SQLite 主文件而忽略WAL。修改前备份是 `backups/pre-v5-20261010-2110`，保留原任务XML和代码。

最小回退：先暂停领取新工作并等共享锁释放；在 `configs/live.toml` 设置 `[competition] enabled=false`，保留其他配置；恢复正常领取。基础采集继续工作，竞争原文/记录/历史不删除。若需完整版本回退，用修改前代码和统一备份恢复流程，先另存当前数据库、竞争证据和本地控制库；恢复后保留“需重新检查来源”的状态。无需卸载或新建Windows任务。
