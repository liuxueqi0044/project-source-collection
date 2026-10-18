# 2026-09-11 系统复审与修复决策

用户要求：反复出现问题时，先审视整个系统，不继续默认都是独立小 bug；使用已授权的真实 QQ 测试账号验收。

## 复审结论

保留现有 DeepSeek provider、RulePack、Memory、Hub、规划与调度、prepare/commit/verify 和持久消息游标。当前证据不足以否定整个业务实现，也不支持把它宣称为可用自动回复系统。需要整理的是部署版本、运行实例生命周期、失败恢复，以及持久身份与临时 QQ 窗口身份的边界。

| 已证实的问题 | 证据 | 修复方向 |
| --- | --- | --- |
| 子进程只 spawn，没有调用现成 HEALTH；supervisor 用联系人表存在判断 ready | run_vm_runtime.py、vm_driver/worker.py、qq_session_runtime_supervisor_guest.py | 启动做实际健康握手；初始化失败明确返回原因；成功观察前不得声称可回复 |
| UIAUnavailable 等错误会留下无法自动恢复的暂停，manual resume 也不接受这种原因 | runtime/state.py 的 apply_observation/resume；bridge.observe_conversation | 区分可恢复的驱动故障和人工/身份/发送结果不确定；只有新的完整观察才能解除前者 |
| session signature 含 QQ PID、启动时间等，却又决定 data_dir | models.py、build_session_observed_runtime_guest.py | 持久账号/联系人/账本与临时定位证据分离；QQ 重启不自动新建记忆和去重库、不自动重放历史 |
| 不同 supervisor 可以创建独立 worker 操作同一 QQ | launcher、supervisor、worker 内存 reservation | 实际运行入口持有跨进程单实例所有权；现有 PlatformMutex 只是 asyncio.Lock，不能充当跨进程锁 |
| 已挂载的 cc8 文件被改动，实际 runtime 又从光盘读取脚本 | cc8 文件时间、VISO 引用、launcher | 新的完整 manifest、一次冻结、复制到 guest 本地后运行；旧介质不作为新交付包 |
| “全部私聊”目前只有配置中的联系人，未知会话没有发现/登记链 | builder、runtime assembly 轮询 | 明确一/两联系人阶段与最终全部私聊范围；不能把授权范围误报为实现覆盖范围 |

部署审查额外确认：A776F94B676596A1DC52F7A78607F1F176AD9C913FAB587BE763BA37B9F51CA1 wheel 中 134 个 Python 文件与当前 src 一致，且有 guest 成功安装报告。旧 UIAUnavailable 报告时间早于这次安装，不能证明新版本已经执行并仍然失败。新的现场执行结果必须重新取得。

## 本轮边界与顺序

1. 恢复真实虚拟机显示和现场，先取得实际入口的输出。显示恢复失败属于平台现场问题，不用应用层补丁掩盖。
2. Sol 修复启动健康、实例所有权和临时观察故障恢复，保持唯一业务发送路径；不引入复杂自动重启，更不在发送结果不确定后自动补发。
3. Terra 整理唯一可诊断入口与 manifest。安装与启动分开，安装完成不自动发送；运行文件落到 guest 本地版本目录。不得反复创建并修改已挂载的介质。
4. 持久身份方案先确定明确契约再实现。session 证据只负责当前 QQ 定位；跨 QQ 进程恢复必须重新核对绑定，不能拿显示名或 RuntimeId 冒充永久身份。
5. 在同一个实际运行路径上，验证真实入站 → DeepSeek → 正确联系人 → 回执；然后验证第二联系人隔离。真实收发已经授权，不重复询问权限，也不再以模拟通过替代实机通过。

## 验收判据

- 运行状态能区别未启动、worker 初始化失败、可观察、规划失败、发送结果不确定与发送确认。
- 同时第二次启动不会再创建一个 QQ 操作实例。
- 一次临时观察失败恢复后可以继续；人工暂停、身份变更和不确定发送不会被顺带解除。
- QQ 重启不会因为新的进程号而遗弃原有数据、重新采用旧消息。
- 最终聊天证据必须对应实际模型生成与实际发送操作。未完成的全部会话发现和跨会话身份能力如实保留为未完成。

这份文件记录审查结论和决策，不代表以上修复或真实自动回复已经通过。

## 当前执行记录（2026-09-11，真实闭环验收前）

- VirtualBox 已恢复运行，QQ 已登录。root 已实际查看 `current-vm-qq-window-2026-09-11.png`：当前为一对一临时会话，输入框为空。恢复时 framebuffer 曾为 0×0，随后恢复；没有执行强制关机或重置。
- Sol 完成真实 HEALTH 启动握手、Windows 进程互斥、worker IPC 事务锁及临时暂停分类。WebUI 只读取缓存健康信息，不再与观察/发送争用同一 Pipe。定向测试通过仅代表代码级检查，不代表真实自动回复通过。
- 发布入口改为 guest 本地 release；安装前后与启动前均核对 manifest 和 venv 模块与 wheel 的对应关系。同版本目录与来源不一致时，在修改 venv 前阻断。
- 发布依赖审查补齐 guest_focus_helper 的两个本地 import；不再隐式依赖旧光盘。supervisor 的 ready 绑定本次子进程与临时 loopback 端口，历史 SQLite 计数仅作为 metrics。
- QQ 窗口的 session 证据与持久账号/联系人记录分离。默认只登记当前明确核对的联系人；第二联系人必须显式加入，不能自动采用磁盘上遗留的 bootstrap-2。

以上为实现与现场恢复进度。完整 QQ → DeepSeek → QQ 与第二联系人隔离仍须后续真实证据验收。

## r05 实机后的系统判断

两个已登记私聊都进入真实运行，第二联系人触发模型规划后被策略判为 review，但未发送；QQ worker 此后处于不存活状态。当前结论是运行链路尚不可靠，不是已完成的产品。此前判断中的发布和启动修复不能替代这一验收。

本轮停止按末端错误逐条猜测。集中处理三处接口边界：

1. **操作进程与主程序。** 当前 worker 首个失败未留存，后续轮询覆盖原因，健康缓存和 HTTP 状态又不能表达操作进程已死。先复用原始 OBSERVE 做一次阶段计时，区别真实读取耗时与子进程自行退出。保留 PID、退出码、请求、阶段和父进程是否主动终止的证据，然后决定生命周期和读取实现如何调整。
2. **模型规划与策略。** 有效候选在 review/blocked/调度拒绝的早退分支丢失，不能解释不发送。统一记录版本、计划、策略原因和最终结果；模型及聊天正文只保存在 guest 本地，状态报告只导出决策元数据。复用现有 policy projection，并把真实入站传入策略。保持阈值，不能因为一次不回复就放宽。
3. **运行状态与验收。** 分开网页可达、程序存活、QQ 驱动有证据可用、业务选择不回复和发送已确认。缺少驱动健康证据就明确 unknown。宿主机 Pause 和 GracefulStop 已各一次实测成功，后者确认进程 exit 0 后才进入诊断。

分工：Sol 负责规划/策略契约和原子审计；Luna 复用实机操作路径做有界性能取证；Terra 负责状态呈现、发布和现场部署；root 复核证据、决定是否改底层读取、最终验收。探针不写游标、不调用模型、不发送，两个联系人各最多一次；首个超时终止后停止诊断，不在死进程上继续轮询。

目前仍是两个明确登记联系人的阶段。全部私聊发现、稳定身份登记及 QQ 重启后重新认证保留为独立未完成模块，不能借授权范围字段宣称已经实现。

## 延期处理的规划与策略契约问题

以下问题由当前源码静态契约直接证实，但尚无证据表明它们就是 r05 不发送的实际原因。新增第三联系人后的真实 `runtime_planner_evaluations` 记录是本次判断的依据；在取得该记录前，不调整策略阈值或发送规则。

- **规则 ID 在 planner prompt 投影中丢失。** RulePack 的 required、prohibited 和 escalation 项投影给模型时只保留规则文本，没有保留规范 `rule_id`。输出 schema 又没有定义 `prohibited_rule_results` 应返回“命中的规则 ID”还是逐项判定结果。Policy 当前把该数组里的每个非空字符串直接当成已命中的规则 ID，并以 `PROHIBITED_RULE_HIT` 和 `PROHIBITED_OUTPUT` 硬阻断。因此 `none`、规则全文或“未触发”等非空结果也会被误作违规；模型同时没有可靠来源生成规范 ID。`must_rule_results` 和 `assumptions` 只进入审计，Policy 不消费；`policy_tags` 虽被投影，当前 Policy Engine 也不据此决策。相反，能够触发 `MANUAL_RULE_HIT` 的 `manual_rule_ids` 没有 planner 输出映射。
- **新联系人事实没有进入 Policy 状态。** LLM request 会从 Memory 投影 `relationship_stage`，但运行时构造 `LivePolicyState` 时没有根据可靠事实设置 `is_new_contact`，因此使用模型默认值 `False`，`NEW_CONTACT` gate 不会触发。这是偏宽松的投影缺口，不能解释一次泛化 review，但会使 RulePack 的新联系人约束与确定性 Policy 执行不一致。
- **`draft` action 没有独立审核语义。** `draft` 与 `auto_reply_candidate` 进入相同的 eligibility 路径；`draft` 标签本身不会要求人工审核。`ignore` 和 `handoff` 则在 PlanningCoordinator 中、进入 Policy 前就结束并兼容记为 `ignored`。因此不能只根据 action 名称解释 review，必须结合审计中的 `audit_outcome`、`policy_reason_codes_json` 和 `non_send_selection_reason`。
- **confidence gate 缺少模型校准契约。** ReplyPlan 只把 `confidence` 限制在 0 到 1，没有说明数值含义或校准标准；strict structured-output schema 又要求模型必须返回该字段。Policy Engine 使用固定 `0.85` 门槛，低于门槛即产生 `LOW_MODEL_CONFIDENCE` 并在无审批时进入 review。默认 RulePack 没有定义或解释这一门槛。真实审计应先记录精确 confidence，再决定是否修订输出说明、校准方式或策略配置，不能因一次不回复直接降低阈值。

- **真实待审核流程尚未接通。** 当前已确认的第三联系人记录为 `confidence=0.76`、`risk=low`，候选正文为“你好呀 在的”，`selection_reason` 提到关系阶段未知；这说明本次 `LOW_MODEL_CONFIDENCE` 有具体模型分数，不能按缺失字段或默认 0 解读。规划层会把该结果记录为 `review_required` 并结束本次 job，但尚未接通从 `runtime_planner_evaluations` 到人工审核、显式重评、重新排程和发送的真实 runtime 入口。UI/hub 里已有的通用 approve 语义不能直接视为本次 runtime planner review 的审核入口；它目前主要覆盖人工草稿或其他控制面，不能替代 review 记录与策略请求的绑定。
- **confidence 的测量对象仍未定义。** 当前契约没有说明分数是在衡量候选回复对当前上下文的支持性、规则符合度、事实确定性，还是发送适宜性。未知关系阶段可能使模型对普通礼貌回复给出较低自评分，这是基于当前记录和字段语义缺口的推断，尚未被真实证据证实；不能据此修改阈值或断言它是本次 review 的唯一原因。

下一模块只做契约和入口设计，不调整阈值：provider 边界增加必填字段检查并保留字段缺失与明确低分的可审计区别；明确 `confidence` 表示“当前候选在已知上下文下的支持性与规则符合度”，未知关系使用中性表达即可，不要求模型为了通过门槛而虚构高分。候选人工审核必须落到真正的 runtime 入口，绑定原始 evaluation、draft snapshot 和最新 policy state，审核后重新执行 PolicyEngine/revalidation，再沿现有 AuthorizationService → DueCoordinator → SendDispatcher 路径发送；不得用通用 UI approve 冒充本次审核，也不得伪造 `HumanApproval`。

诊断顺序固定为：先看 `audit_outcome`；若为 `review_required`，核对 risk、confidence、敏感主题和提示注入原因；若为 `policy_blocked`，核对 `prohibited_rule_results`、确定性输出规则和硬状态原因；若为 `ignore` 或 `handoff`，读取 `non_send_selection_reason`。这些映射用于解释真实结果，不构成对当前失败原因的预判。
