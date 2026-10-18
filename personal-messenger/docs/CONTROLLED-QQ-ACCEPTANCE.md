# 可控 QQ 会话真实验收

日期：2026-09-11。本文记录固定验收对象及判据，不代表已经通过。

## 已核对的现场

- 虚拟机官方 QQ 当前登录账号：3179233661，显示名“小狐狸”。
- 用户能控制的发送账号：3511875663，在当前账号中备注“妈妈”。备注只用于人工导航，不作为身份键或关系事实。
- root 已查看官方 QQ 资料/聊天证据：该已有好友私聊在16:16收到一个气泡，内容为“你好，你好”。不能把两个“你好”字样误记为两条独立消息。
- 该会话此前没有登记进 runtime。原有 session-contact-1/2 是其他联系人，不能覆盖其编号、游标或记忆。
- r06 已经正常停止，QQ保留在此会话。此前“未见新消息”只是原列表视口没有显示，不能解释成QQ未收到。

## 此次接入契约

通过通用人工追加登记工具新增独立联系人，计划使用 session-contact-3 / qq-session-conversation-3。配置保留已有1/2，使持久runtime行与driver绑定保持一致；不通过删除旧数据限定测试范围。

普通新增联系人默认只建立消息基线。本次用户已经明确发送了真实测试消息，允许对已核对的最新入站做一次显式采用，让用户不必再次发送。采用证据须关联bootstrap run、联系人和当前会话scope，固定消息数量、最后方向及文本SHA256；首次观察不匹配时停止，不采用其他消息、不建游标。游标已经存在时不能再次采用。

## 通过判据

1. 接入前后的1/2身份与游标保留，3的绑定与官方UI核对一致。
2. 这条已核对入站只进入一次处理流程；没有历史批量重放。
3. 真实DeepSeek返回的计划、策略结果及原因可追踪。忽略/转交/待审核必须如实报告，不能算发送成功。
4. 若策略允许发送，走现有Hub及prepare/commit/verify流程；最终在正确私聊里看到与模型候选一致的新出站气泡，并有对应发送记录/回执。
5. 故障时保留本次run、worker请求、错误和阶段；结果不确定时不自动补发。

此次通过后，仍需处理全部私聊自动发现、持久身份登记和读取效率。这些不能由一个可控会话的成功替代。

## r07 登记实证（16:52）

r07 于08:42:28 UTC安装成功。首次真实bootstrap读到了正确消息，但在header_geometry_invalid处失败；操作方在启动后又点击了窗口最小化按钮，与脚本自行切换QQ前景存在竞争。随后一次长命令因键盘重复字符输入失败，未启动bootstrap，不能算业务失败。

改用经GuestFile往返逐字节/哈希核验的短启动文件，并在Enter后完全停止鼠标键盘注入后，第二次真实bootstrap成功：run `016b205b-b9e9-440d-933d-619ed426c699`，08:51:19至08:51:58 UTC，38.891秒。消息count=1、末尾inbound、长度5、SHA256=`57d84e23bd29ec22f546ea6b6f3ac59460ce19d1731a4e51f9cb838fd8d44417`。

root已核对session-contact-3、PID7352、HWND393974、process-start `134334574244723217`均匹配，participant_signature为`qq-session-observed:7d2baf8289f4eab75e1faafcd63b293eae1c5086661eafe9ecde2d0be4a1c76b`，与旧1/2不同。报告为宿主机`outputs/qq-vm/install/diagnostics/r07-bootstrap3-attempt2.json`。已批准BuildAndStart保留1/2并新增3、只对3显式采用该入站，启动先保留暂停。尚无本次模型/发送结果。

## r07 真实运行结果：首联系人读取超时，未通过

runtime run `64977509-59cc-4081-b8f7-e16ff5a1acfa`，PID7364、child5676，启动HEALTH成功。核对3个配置绑定及仅3的一次采用证明后，单次Resume请求`e8f2c9ba-6c2a-4e02-a794-226f3a91fac6`获接受，之后没有继续注入UI操作。

真实业务首个OBSERVE针对session-contact-1，request `f203f699-ebfd-4f36-aee5-68c47c8e80c6`，耗时45041ms，触发worker_timeout_isolated；父级request_timeout终止child，exit=-15，完成于08:57:30.973485 UTC。安全原始事件保存于`outputs/qq-vm/install/diagnostics/r07-worker-terminal-event.json`。后续3个联系人均成为worker_not_alive，尚无本轮成功观察、模型审计、发送或回执。不能称contact3已进入模型，也不是子进程无原因自行崩溃。

另一个问题是worker witness在08:54:39 UTC之后停止更新；源码确认publisher异常不受主任务监督且被最终gather吞掉，Windows文件替换异常也无恢复边界。但本轮没有保留下该publisher的实际异常，不能断言必由文件读取竞争造成。不要将其与明确的45秒读取超时合并成一个原因。

单次graceful_stop请求`33c967c9-fccc-4855-949c-6b86b70108f2`已正常停止本run，exit=0、Web关闭。后续批准的模块是：监督publisher并记录安全异常；UIA读取按选择前/后独立阶段建索引，减少8次重复全树扫描至2次，同时缓存阶段内属性及所需pattern。每次命令和UI修改后必须重新取证，不跨联系人或发送动作复用；增加真实业务阶段日志。保持45秒限制、联系人顺序、身份/群聊校验、采用证明及策略门槛。尚未完成该模块部署与真实验收。

## r08 实际结果（覆盖上段模块待部署状态）

r08已安装并运行，run `52821b69-2e90-440d-862d-1ecd36af3d79`，runtime PID1464、worker PID880、Web55741。Resume请求`75bb244a-c8ba-4b07-b83f-f37e84bda664`获接受。worker阶段日志证明首轮1/2/3均成功读取：contact1 discovery/verification为5027/5625ms，contact2为5337/5342ms，contact3为6060/10182ms；后续contact3一次完整OBSERVE为11238ms。正常读取开销确有改善。

随后contact2的OBSERVE request `3deb0f3e…`在discovery_phase卡住，parent以request_timeout终止worker，elapsed=72676ms、exit=-15，17:47:51本地时间。不能将此前成功解释为持续健康。此轮已出现75条UI阶段事件，均为OBSERVE，未见PREPARE/COMMIT/VERIFY。安全证据为宿主机`qq-runtime-failure-summary.json`和`r08-post-resume-readonly.png`。

supervisor状态文件仍停在17:42的启动暂停快照，实际worker witness和业务阶段日志更新到17:47，因此旧状态中的空model/send计数不能证明未调用DeepSeek。正在执行固定只读guest数据库审计，结果待取回。不要清除contact3游标或重做bootstrap。当前worker已终止，runtime是否仍活及实际planner结果由该审计核定。

后续架构方向：只允许对没有任何非终态发送操作的OBSERVE超时进行有界worker替换；失败联系人持久隔离、失败请求不自动重试，其余联系人继续；每run替换预算有界，并保留旧故障。另修supervisor状态发布与主进程生命周期，避免导出文件静默冻结。该恢复模块尚未实现。

现场额外约束：宿主机Win32_Battery查询显示未充电、电量8%；已提醒用户接电源。任何继续部署应先核对供电状态，并优先保存工作与虚拟机现场。

## r08 模型结果与诊断结论更正

用户已接电源，宿主机复查BatteryStatus=2、电量9%。前面的超时发生于未充电/低电量期间；供电状态是性能分析的额外变量，接电后的稳定性仍待验证。

`r08-live-audit.json`直接读取数据库，确认contact3确实调用了DeepSeek：request `757bd6af-249f-4eee-a9fa-0dd8b6e76125`，model `deepseek-v4-flash`，7745ms，action=auto_reply_candidate，outcome=review_required，reason codes=[LOW_MODEL_CONFIDENCE,APPROVAL_MISSING]。发送操作/回执为0，三个cursor均已存在next_seq=2。因此不得再次用bootstrap采用这条消息；后续若重新规划，须走显式、可审计的重评机制或使用新入站。

该临时诊断脚本有两处错误，root已暂停复用并交Sol修正：其一，从runtime_plan_artifacts而非runtime_planner_evaluations.plan_json提取候选，导致controlled_contact3_plan=null，不能据此断言模型没有回复文本；其二，使用os.kill(pid,0)作为进程存在检查，这在Windows不是只读查询。CPython 3.13对应分支会尝试GenerateConsoleCtrlEvent，异常被脚本错误地解释为进程不存在。旧process_exists=false结果作废，实际影响尚无证据，不得断言已杀死或原先不存在进程。参见[Python官方说明](https://docs.python.org/3/library/os.html#os.kill)和[CPython 3.13实现](https://github.com/python/cpython/blob/3.13/Modules/posixmodule.c)。固定诊断改用带正确类型声明的Win32只读进程句柄查询，并区分unknown/access-denied与不存在。

## 修正审计结果（18:15）

root复核Sol修正后的Win32只读查询，额外修正WinDLL(use_last_error=True)必须使用ctypes.get_last_error的语义；Terra完成guest脚本覆盖和回读哈希验证后，只执行一次短诊断启动文件。新审计SHA前缀09FF815C，written_at=2026-09-11T10:15:49.886010+00:00。runtime PID1464查询为alive，worker PID880查询为exited，HTTP200；此前父进程不存在的推断不成立。

contact3计划已正确读出：回复“你好呀 在的”，confidence=0.76、risk_level=low，无禁止规则命中、无敏感分类。模型selection_reason提到首条问候、关系阶段未知、保持普通友好。策略阈值为0.85，因此实际进入LOW_MODEL_CONFIDENCE/APPROVAL_MISSING；不是confidence缺失后默认0。发送操作与回执仍为0。三个游标next_seq=2保留。

源码另确认真实runtime尚未接通规划review_required后的审核/重评/排程入口；WebUI中相似按钮仅fake facade支持，不能当作真实审核能力。此处是产品流程缺口，不能靠重放旧消息或伪造HumanApproval解决。

当前分工：Sol实现严格有界的只读OBSERVE故障隔离/worker替换；Terra修supervisor发布容错并保持主进程生命周期监测；Luna补模型合同与真实审核缺口的文档复审；root集成验收。尚未部署上述新模块，尚未通过真实自动发送验收。

18:20已正常停止r08：单次graceful_stop请求`ae067265-d7ca-4aae-91ec-a0885b6d7c29`获accepted/stopping，worker witness同run记录STOPPING。停止后只读审计确认runtime PID1464和worker PID880均不存在（OpenProcess winerror87），原Web端口TCP不通，无新增发送操作/回执。QQ和VM仍保持运行与登录，没有重启、重放或重新Resume。

## r09 发布批准（源码验证完成，实机验收待执行）

root批准Terra冻结并安装r20260911-09。本版仅包括以下三个边界明确的修改：

- Sol：OBSERVE超时严格匹配请求、联系人、父级超时原因和已知旧child退出后，持久隔离该联系人；无非终态发送操作时，每run最多启动一次后继worker，并强制HEALTH成功后接管。失败读取不重试、不改游标；发送失败不触发此恢复。所有UI请求共用代际锁，未激活child仍由bridge持有并在取消/关闭时清理。历史故障保留原run，当前健康只代表当前worker。
- Terra：supervisor状态发布覆盖序列化、建目录、临时写入和原子替换；仅Windows5/32/33短暂错误最多4次，总退避0.3秒。启动前完全失败不spawn，启动后发布失败继续监测原进程；首次失败、恢复及最终退出有安全日志。该模块不等同于所有supervisor异常都已覆盖。
- Luna：说明confidence评估当前候选的支持性、适配性与规则符合度，不是对联系人了解程度。关系未知采用中性表达；真实上下文缺失影响候选时降分或handoff，不得为通过门槛虚增分数。保留0.85阈值和旧审计。此说明能否改善实际问候结果仍待新入站验证。

对应定向测试：bridge和CLI共42项、supervisor11项、LLM契约5项通过。root完成源码与边界复核；未重复扩大测试，未把代码测试当作QQ发送成功。start9保留联系人1/2/3，不传AdoptLatestInboundIndex。安装后先暂停核对版本/身份/HEALTH，之后才能Resume；下一次模型与发送验收使用新入站，不重新采用旧消息。

## r09 已部署，等待新入站闭环

r09 wheel SHA256为`5F401B4C94AF0054EAE3E0B85601A14BA8A14C6C9312B9CBBDD4703582A1F44F`，已冻结为`r20260911-09`并完成安装。新runtime run为`783a6e92-7afe-471a-9e17-6aa1798717fe`；暂停启动HEALTH成功（1503ms），随后单次Resume请求`37a3fc72-7e32-43a2-ab24-e107015a3a58`获accepted/running。此处runtime进程字段为PID5016，不能误称为supervisor PID。

Resume后worker generation=1、PID2964，状态为available；无当前或历史terminal failure、无recovery、无quarantine。安全阶段日志记录contact1/2/3各已完成至少4次OBSERVE verification phase，最新一轮分别为1808ms、1552ms、1737ms；没有terminal event。该结果只证明三条已登记会话的读取恢复稳定，不能外推为全部私聊自动发现。

本轮仍仅登记session-contact-1/2/3，保留已有cursor；start9没有采用旧contact3入站，也没有重做bootstrap。旧contact3模型记录仍是review_required，不能重放。用户将发送一条新的可控入站后，才进行一次真实模型、策略、prepare/commit/verify及官方QQ出站气泡/回执的闭环验收。此文档在该闭环前不得声称已自动发送成功。

## r09 等待新入站期间的宿主待机故障

用户随后表示新消息已发送。当前审计仍只有旧contact3 review，发送操作/回执为0，不能声称新消息已被模型处理。r09最后成功OBSERVE在18:52:27；contact3请求`84a316fa-4239-45ae-9cb1-7f9e6ec1b6e8`随后触发worker_timeout_isolated，elapsed=1453346ms。唯一后继worker的HEALTH也超时（763861ms），因此c3已隔离、worker不可用；有界恢复没有擅自循环重启。

Sol只读核对宿主System事件及VBox.log，确认宿主自动Modern Standby：本地时间18:54:55.804进入、19:19:11.741退出，约24分16秒；19:19:11.742再次进入、19:31:59.239退出，约12分47秒。VBox对应记录约1459秒和763秒的时钟追赶及约1450秒的guest heartbeat中断，没有独立VM Paused/Suspended状态。宿主Silent方案交流电STANDBYIDLE=300秒、VIDEOIDLE=300秒，直流均180秒；进入待机时间与交流5分钟策略吻合。

这是此次超长OBSERVE及HEALTH的已证实环境原因。宿主待机使VM/worker无法获得调度，Pipe.poll(45)只能在恢复调度后返回，而perf_counter计入停顿；不能通过加超时或加重启次数解决。guest日志时间在恢复阶段有偏差，因果时序优先采用宿主事件与VBox基准。截图时钟停在18:52也不能证明桌面未冻结；Terra此前“无freeze”推断已作废。

root已告知用户，并询问长期接电睡眠偏好；未获答复前不修改电源方案。临时验收采用只请求SYSTEM_REQUIRED的30分钟宿主唤醒进程，不要求显示常亮，不阻止用户主动睡眠；使用[微软SetThreadExecutionState接口](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-setthreadexecutionstate)，退出时释放。后续先正常停止r09，再通过严格离线CAS和审计解除这次环境故障造成的隔离；保留全部游标、模型审计与消息，不bootstrap、不重放。该运维解除能力拟随r10交付，尚未执行。

19:45现场恢复：临时wake hold进程PID38492启动成功，记录到期20:15:05；SetThreadExecutionState返回成功，进程存活。powercfg /requests因需要管理员权限返回1，因此该查询不能作为唤醒请求不存在的证据；未提权。r09单次graceful_stop请求`a8bb8baa-3710-423e-aa65-794a99300c1d`获接受，后续同run状态graceful_stopped、exit0、Web关闭。随后仅注入一次无文字Shift按下/释放恢复guest显示，截图时钟更新至19:45。

root已实际查看`r09-post-stop-shift-wake.png`：妈妈会话新增一个入站“你好呀”（QQ显示19:07），旧16:16“你好，你好”仍在，输入框为空。新消息已在官方QQ中可见，尚无模型/发送记录；无需用户重发，后续解除隔离后应由普通增量游标处理，不进行首次采用或重新bootstrap。

## r10 隔离解除设计（已批准，尚未执行）

root已批准r10源码，Terra可据此冻结发布；本节记录的是经复核的运维能力和待执行参数，不表示guest数据库已经改变，也不表示新入站已经进入模型或发送流程。

r10新增离线工具`scripts/release_qq_observation_quarantine.py`。工具必须由与runtime相同的Windows用户运行，并先取得runtime使用的同名单实例mutex；runtime尚在运行时明确拒绝。随后在一个SQLite `BEGIN IMMEDIATE`事务内精确核对expected run、conversation、binding、binding revision、request和failed generation，并确认`qq_vm_ops`没有PENDING、PREPARED、COMMITTED等非终态发送操作。任一条件不符均不写入。

解除不会删除故障事实。它向append-only的`qq_vm_observation_quarantine_releases`写入release ID、原隔离的完整安全元数据、operator ID、reason code和UTC解除时间；重复相同请求只返回already_released。bridge只把没有对应release marker的隔离视为活动隔离，witness中的活动隔离计数随之归零，但历史terminal failure继续携带原run、generation和解除摘要。以后同一会话若发生新的、独立确证的只读超时，可使用新的活动隔离槽，旧release审计仍保留。

runtime状态恢复分两步完成：活动隔离期间bridge仍返回`complete=false`，所以会话继续暂停；release marker生效后，runtime下一次轮询必须取得一次完整新观察，才允许清除`driver_quarantine:read_only_observe_timeout`或`driver_quarantine:binding_changed_requires_release`。manual pause、identity guard、binding revision pause和SEND_UNCERTAIN不在自动清除范围。该流程不跨bridge/runtime两个数据库做伪原子更新，也不触碰cursor、消息、planner或旧审计。

本次待执行目标参数如下：

- SQLite：`C:\PMAI\data\runtime\qq-default-account\qq-vm-bridge.sqlite3`
- expected run：`783a6e92-7afe-471a-9e17-6aa1798717fe`
- conversation：`qq-session-conversation-3`
- binding：`session-contact-3`
- binding revision：`1`
- request：`84a316fa-4239-45ae-9cb1-7f9e6ec1b6e8`
- failed generation：`1`
- reason code：`host_power_suspend_confirmed`
- operator ID：由实际执行者显式提供并进入审计，不从显示名或联系人资料推断

定向验证覆盖精确CAS错误拒绝、runtime mutex占用拒绝、非终态发送保护、重复解除幂等、失败读取不重试、每run一次恢复预算、pending successor取消清理、历史故障保留，以及“release后完整OBSERVE只摄取普通增量新消息、清除对应隔离暂停并建立planning job”。bridge、离线CLI及相关runtime witness测试合计45项通过；相关模块py_compile、CLI真实import/`--help` smoke和限定`git diff --check`通过。该验证不等同于真实QQ闭环；r10尚未安装，解除命令尚未执行。

## r10 已部署及误阻断事实记录

以下为本轮已提供的运行记录，不改变前文历史设计时态，也不把计划中的能力写成已成功执行：

- r10 已部署 wheel，SHA256 为 `74BB18CA54E59AB14BD734EE02C64D7392597F7C781DAD1D7364E8095720F6AF`；对应 runtime run 为 `b52d6f0d-e756-4d89-a5c7-b65effa13cfe`。
- 隔离 `4fabb90a-758b-4c81-8aa6-52c296329aa0` 已于 `11:57:49.873870 UTC` 完成解除；历史记录保留，当前 active quarantine 为 `0`。
- `12:01:16 UTC` 的 resume request `f9f205d8-e12d-44cd-b08d-d4c32e8e0fac` 获 accepted。contact3 的 OBSERVE request `3c0085dc-ba71-4a9c-883d-800af9ba90a7` 耗时 `4228ms`。
- 新入站“你好呀”对应 evaluation `1f636f5f-2834-4299-b6f8-3d99dc8040dc`，时间 `12:01:38.292634 UTC`，模型 `deepseek-v4-flash` 耗时 `5794ms`。结果为 `policy_blocked`，原因 `PROHIBITED_RULE_HIT/PROHIBITED_OUTPUT`；记录中的所谓 rule IDs 实际是“未涉及转账、礼物、见面、隐私或身份验证”和“未虚构个人事实或跨联系人引用”，并无敏感分类。发送回执为 `0`。
- 该新消息已经被消费，不能通过游标重放。计划中的一次显式、可审计重评及关闭本次误阻断开关尚未实现、尚未部署，不能写成已成功；Sol 正在处理 provider 禁止结果投影开关及显式重评边界，期间不扩展其他契约。
- `20:06 UTC` 前已提交 pause request `76d431a6-8169-458c-8676-81c445acf650`，结果为 accepted。Terra 正常 stop，准备后续部署。

当前范围仍是 3 个已登记私聊，并非全部好友发现。后续若进行重评，必须保留本次 evaluation、消息消费 cursor 和误阻断原始审计，使用显式新 request 链接到原 evaluation；不得伪造发送回执或把重评计划写成已完成闭环。

## r11重评已准备、r12时效锚点修复待部署

r11已安装成功。离线inspect对原误阻断evaluation返回`ready_for_prepare=true`，随后唯一一次显式重评已准备：reevaluation ID `b3582f56-8c43-4b2a-bc9e-fc275e1f9120`，binding revision为1、conversation revision为3、当前global revision为10、source keys SHA256为`f3e9d157db90de53c2fe0b25b6ec29065b4f6e32179b5e8777a30c403d2c893e`。原evaluation、消息和cursor均保留。到此尚未启动runtime、Resume或调用模型，也没有新计划、发送操作或回执。

启动前root发现具体时效缺陷：`PacingScheduler`以`first_inbound_at + plan_ttl_seconds`计算计划过期，而当前`PacingConfiguration`使用`PacingProfile`默认300秒；原消息时间远早于本次显式重评，因此r11即使重新调用模型，也可能立即得到`WINDOW_AFTER_EXPIRY`，不能完成排程。Terra据此停在Resume前，没有进行一次已知徒劳的API调用。

r12只针对带`reevaluation_id`的显式重评job修正pacing锚点：ScheduleRequest的first/last pacing时间改用本次重新规划的`now`。普通新入站仍使用真实消息observed_at；Hub中原消息时间、LLM上下文、source keys、cursor、CAS和防重复语义均不改变。唯一集成测试覆盖“原入站已过去600秒→显式重评→仍生成未过期waiting计划”，同时确认Hub原observed_at不变且send_operations为0；该测试通过，相关py_compile和限定diff检查也通过。root已完成源码复核并批准r12冻结安装，但本段记录时r12尚未安装、尚未调用模型，更未发送成功。

## r11 内容策略开关与一次重评（源码已批准，实机待执行）

用户明确要求当前QQ先取消内容规则造成的阻断。r11新增单一配置`content_policy_checks_enabled`，默认值为`true`，因此其他部署保持原行为；本QQ session builder显式写入`false`。关闭时不再以模型risk等级、confidence、provider报告的禁止规则、本地禁止正文匹配、manual rule、敏感分类、prompt injection、minor或new-contact内容判断触发`PROHIBITED_RULE_HIT/PROHIBITED_OUTPUT`、内容审核或低置信度审核。LLM prompt和RuleProjection同时停止投影对应required/prohibited/escalation/negative-example内容层，保留人设身份、语言、语气、长度、记忆、正向示例和pacing。

该开关不关闭结构化输出验证，也不改变模型主动选择IGNORE/HANDOFF的语义。QQ direct身份认证、群聊排除、注册范围、whitelist/automation、人工/全局暂停、消息鲜度、expiry、binding/conversation/global revision CAS、游标去重、单实例owner、prepare/commit/verify和SEND_UNCERTAIN保护均保留。每条`runtime_planner_evaluations`写入`content_policy_checks_enabled`的0/1值，便于核对实际生效配置。本轮没有修改0.85常量；关闭模式是不消费该内容置信度审核，而不是伪造或抬高模型分数。

r11另新增离线工具`scripts/re_evaluate_planning_job.py`，仅用于本次已消费且错误blocked的evaluation。`inspect`以SQLite只读模式输出conversation、binding/conversation revision、原/当前global revision、source-keys SHA256、latest/current/job匹配和无发送条件，不输出消息正文、完整provider request或候选正文。`prepare`先取得与runtime相同的Windows单实例mutex，再精确核对原evaluation仍为同revision最新完成记录、source keys仍匹配同一会话、当前binding/conversation revision和调用方单独提供的当前global revision。原evaluation的global revision可以因正常Pause/Stop与当前值不同；当前global paused允许，但contact pause不会被清除。

prepare还拒绝与source keys有任意重叠的Hub send operation、该会话除failed/cancelled外的任何`qq_vm_ops`、同source plan artifact或segment execution。通过后只把现有conversation主键job从failed原子改为pending，保留并继续递增attempt_count，不改cursor、observation或旧evaluation；独立reevaluation UUID形成append-only审计，正常planner随后生成新的evaluation UUID。新入站若先到，会原子把prepared重评标为superseded并清除job关联；provider失败、恢复失败、stale或正常evaluation都会终结该审计，避免永久prepared或把旧重评关联到下一条消息。

定向验证共18项通过，覆盖默认开启兼容、关闭后各内容gate不再阻断、prompt内容层移除、开关审计、旧数据库非破坏迁移、配置严格布尔校验、原/新evaluation保留与关联、attempt_count单调、Pause/Stop造成的global revision变化、新入站supersede、source-overlap发送拒绝、builder配置，以及CLI import/`--help`、相关py_compile和限定diff检查。root已完成源码复核并批准Terra冻结安装。本文记录的仍是已批准源码和离线准备；r11尚未在此记录中证明inspect/prepare、模型重评、调度、QQ发送或回执成功，绝不能据此声称真实闭环完成。

## r12 实机结论

- r12 wheel SHA256 为 `D422D2B64612CF2ADE798F95AD4695C926AF87ADC3F93117DB50E5DD6717FF62`，与 `137py` 一致。runtime run 为 `6770d2f3-7cc9-4548-9537-2a15881772de`，runtime PID `7364`，worker PID `8868`，generation `1`；HEALTH 耗时 `2063ms`，quarantine 为 `0`。
- Resume request `84330827-8668-48c0-98b3-feb13f8b3477` 获 accepted。新 evaluation `ded898ee-8117-4005-b9a3-9a09faa30576` 的 DeepSeek 耗时 `3766ms`，结果为 `auto_reply_candidate/scheduled/ELIGIBLE_LOW_RISK`，`rule_ids` 为空。由此确认原内容阻断已通过；本次 app 内容审核关闭已经实际部署生效。
- 实际发送仍失败。本地已有 `../qq-vm/install/diagnostics/r12-send-chain-summary.json`（相对 project parent 再进入 `qq-vm`），其中记录 operation `986b4570-66f4-4a3f-86d8-0b3bb1e427ab`；Hub/bridge 结果为 `FAILED_SAFE`、`commit_intent=0`，receipt 为 `0`。PREPARE request `25fad3fb-4609-40ce-a2a7-12eb16dd36c8` 已完成 target、discovery、select、verification 阶段，没有 COMMIT/VERIFY。
- 当前代码 `bridge._prepare_send_locked` 折叠并丢失 worker 具体错误，不能据此臆测 composer 根因；preinsert guard 已通过。后续应先保留 PREPARE 的具体失败原因，并用明确身份的实机输入框证据定位。
- Pause request `ae53c34d-2e5d-411f-979e-6518e4523f19` 获 accepted；graceful_stop request `6937a95f-1c42-46cf-b267-b0cb0c984131` 获 accepted，随后 exit `0`、Web `false`、current `stopped`。临时 hold PID `42444` 的 sentinel 已正常于 `20:52:01` 停止，未修改电源方案；QQ/VM 仍保持登录开机。
- 本轮未重试、未再次调用模型、未运行新 probe；旧 cursor 和审计保留，旧 operation 不得重置或盲目重放。当前只能确认“内容审核已通过、发送未通过”，不能称该账号已经自动回复成功。下一步先定位发送 PREPARE 失败，不再调整违规规则。

## 2026-09-12 分辨率与窗口状态复审

现场图证明旧、新两轮 QQ 均为最大化普通窗口，可见 QQ 标题栏和 Windows 任务栏；变化来自 guest framebuffer 从 `1023x768` 增至 `2560x1429`，不是一次普通窗口、一次最大化。旧 supervised helper 以 root-relative `x/y` 比例筛选聊天标题，标题横向比例因此从约 `0.318` 变为 `0.128`，触发 `HEADER_NOT_UNIQUE`。

生产 `session_identity._header_digest` 已改为使用唯一 composer 约束标题位于同一聊天面板，并从持久 digest 中移除 root-relative 几何；digest 只保留结构与规范化标题的 SHA256。联系人标题改变仍会改变 digest，已登记 row runtime-id、QQ process/HWND/start-time、group marker 与唯一候选失败关闭均保留。定向验证连同 composer/single-send 相关用例共 31 项通过。

进一步的真实窗口切换发现 `guest_focus_helper.py` 仍硬编码 `is_zoomed=true`。该前提已改为接受可见、非最小化、尺寸至少 `480x420` 的 QQ 顶层窗口；有唯一 foreground 时选它，否则只在候选唯一时选择，多候选继续失败关闭。新版本 `r20260912-03` 已安装：wheel SHA256 `92D3C6610D0E6F9EC78CE33FDF2498397CFA45AE4626049E0E95AAF5B67DF643`，manifest SHA256 `67314B67F3877771C128E8F1D2983557F28EC89DCF3AEA66612E7EAC5A507D31`，guest 安装状态成功且安装后模块 hash 与 wheel 一致。

同一 QQ PID `10596`、HWND `197750`、同一 session-contact-3 先还原为 `820x612`，再恢复最大化约 `2576x1397`。两次真实 bootstrap 均成功，header digest 均为 `9d4db2c12f25d852f932405deb14dfc263624e08a47c753d8da44974f415b983`，selected row digest 均为 `96fa3c6c78c1d9ca55d8219a33efa2ceaf61e3b50b0a0a76cef98dc3af62d5a4`，group marker 均为 0，bubble count 均为 4。测试未写 composer、未发送消息，结束时已恢复最大化。

证据：`../qq-vm/install/diagnostics/qq-window-presentation-normal-9b43d6da0e3946c29748eee94e788971.json`、`../qq-vm/install/diagnostics/qq-window-presentation-maximized-30d11ee63348456ab8493cc6bfe54b4d.json`、`../qq-vm/install/diagnostics/qq-session-observed-bootstrap-3-1e3b2156fd5c4bfda51cc71acb7fa55e.json`、`../qq-vm/install/diagnostics/qq-session-observed-bootstrap-3-37d3f9552efe44ffa934b406cd898ce2.json`。

当前完整 runtime 仍停止。session-contact-3 已按当前 QQ process 重新采集；session-contact-1/2 尚需切换到各自私聊并刷新当前 process evidence。此处只证明窗口状态兼容和只读会话识别，不代表 DeepSeek 自动回复闭环或全部私聊自动发现已经完成。

## 2026-09-12 r15-r17 会话选择验收结果

窗口状态复审结论保持不变：旧、新现场 QQ 都是最大化窗口，分辨率分别为 `1023x768` 与 `2560x1429`。当前实现也已真实证明普通 `820x612` 与最大化窗口都能完成 contact3 身份读取，因此窗口呈现不是本轮选择失败原因。

选择层分成两个问题验证：

1. r16 已证明 worker 刷新时旧、新 UIA 进程不再重叠，刷新风暴消失。
2. r17 已证明 `LegacyIAccessiblePattern.DoDefaultAction()` 不是稳定的多联系人动作：contact1 可完成读取，contact2 在两个独立进程中仍未确认选中。

r17 run 为 `58214b15-a528-482e-8476-8724c4367f2b`。contact1 的真实 OBSERVE 完成 target、foreground、discovery、select、verification、bubbles；contact2 两次动作后仍无 selection verification；contact3 为 `worker_not_alive`。runtime 随后正常停止，exit `0`。本轮没有 composer、COMMIT、VERIFY、出站气泡或回执。

隔离诊断结果：

| 动作 | contact2 结果 | 是否产生输入 |
| --- | --- | --- |
| Legacy default action | r17 生产中两次未确认选中 | 调用了 Legacy 动作；没有消息输入 |
| SetFocus + Enter | 行不可键盘聚焦，前置检查失败终止 | Enter `0/0` |
| 动态坐标单击 | runtime-id 点命中不一致；目标 row 又缺 UIA Name，身份检查终止 | SendInput calls `0`，mouse input `0/0` |

主要证据：`../qq-vm/install/diagnostics/qq-legacy-default-action-r20260912-16-ea7897e63d5f4308a42cd4d47149df10.json`、`../qq-vm/install/diagnostics/qq-focus-enter-r20260912-17-97557cac1f15487383def0979344bb2b.json`、`../qq-vm/install/diagnostics/qq-dynamic-click-r20260912-17-0040ef0b044b4930ba7bb8cb4bade3bf.json`、`../qq-vm/install/diagnostics/runtime-status-host-554dc53d01dc4ff3a34886ca08c7b5cb.json`。

验收决定：r17 不通过多联系人自动回复启用条件，保持停止。下一轮先单独重构并验证会话选择 actuator；在 contact2 selection-only 成功、全新进程身份确认成功、三联系人真实只读轮换稳定之前，不启动完整回复 runtime。receipt count 仍为 0。
