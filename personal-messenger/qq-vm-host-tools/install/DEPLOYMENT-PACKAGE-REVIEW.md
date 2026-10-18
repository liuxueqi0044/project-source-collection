# QQ VM 部署包只读评审

评审对象：`outputs/personal-messenger-ai-v5`（代码离线验收记录为 534 passed；该数字是历史验收结果，不代表本次 Windows/QQ/DeepSeek 实机已验收）。本评审未启动 VM、未下载或执行外部执行器、未读取 credential-state/API key，也未修改 runtime。

## 结论与阻断

当前不能把仓库直接当作“官方 QQ + DeepSeek 多联系人”可启动包。必须先在 Windows guest 中用实际 QQ 版本、guest 环境和人工核验结果生成部署数据；示例中的 `REPLACE_WITH_*`、空 selectors、`profiles/runtime-v5.example.json` 的旧字段和 `rulepacks/personal-default-v1.yaml` 的 DRAFT 状态都不能用于生产启动。

项目 `pyproject.toml` 的 Python 要求是 `>=3.12`。但当前 `scripts/deployment/guest_bootstrap.ps1` 固定调用 `py -3.12 -m venv`，所以按现脚本部署时 guest 必须安装并可由 Python Launcher 解析到 Python 3.12；这不是项目要求 Python 3.13，也不是说 3.13 不满足项目要求。若 guest 只有其他版本，现 bootstrap 会失败，需先修改脚本再重建部署包。

真实入口是 `scripts/run_vm_runtime.py`。它没有独立的“模型 CLI”：DeepSeek provider 在 runtime 组装时读取配置的 `model`（默认 `deepseek-v4-flash`），而 API key 由当前 Windows 用户 DPAPI vault 的 `deepseek.api_key` 提供。`--check` 不调用 DeepSeek API、不启动 QQ/worker/WebUI，但仍要求 vault/key 可解密，并且必须通过真实 contact、每联系人唯一 binding、完整 guest selector pack、匹配且健康的 `guest_foreground` capability，以及 `data_dir/rules.sqlite3` 中可解析的 active RulePack。因此“模型 CLI 可用”不能绕过真实配置绑定；缺任何一项都会拒绝。正常启动还会创建/读取 runtime authorization HMAC key 并启动 worker。

## 最小可复制包

应复制源码和可审计的部署元数据：

- `pyproject.toml`、`src/`、`scripts/run_vm_runtime.py`、`scripts/inspect_qq_guest_bindings.py`、`scripts/deployment/`（含 bootstrap、诊断、ISO 校验、启动检查、runtime 示例和 DeepSeek vault 配置脚本）；运行源码方式需按 README 设置 `PYTHONPATH=src`，或在 guest venv 中以项目安装方式运行。
- `rulepacks/` 只作为审阅来源；guest 的 `data_dir/rules.sqlite3` 必须由正式流程导入/激活一个已批准的 M7 RulePack。仓库的 `personal-default-v1.yaml` 当前是 DRAFT，不能激活或宣称已认证。
- `profiles/qq/qq-9.9.26-local-certified.json` 只能作为已记录环境的参考 profile；部署时必须核对 guest 实际 QQ 可执行文件/签名、进程签名、窗口类、QQ 版本、DPI、显示器拓扑、主题和最大化状态。`profiles/runtime-v5.example.json` 不是生产配置模板；实际应从 `scripts/deployment/runtime.example.json` 复制后填充真实值。
- `scripts/qq_uia_probe_helper/` 的源工程/协议及其已构建 helper（若选择复制构建物）须保持版本匹配；不要把 `bin/obj`、缓存或未经当前 guest 验证的 helper 当作认证证据。
- 需要为每个联系人准备 `contact_id`、唯一 `hub_conversation_id`、`account_id`、QQ 内部会话 ID、人工核验的 participant signature、唯一 binding ID；多联系人必须一一对应、会话 ID 与 binding ID 均唯一，身份不确定时暂停。

## 必须排除

不要复制或打包 `credential-state`、DPAPI vault 内容、任何 API key/HMAC 明文、`state/` 运行数据库、`*.sqlite*`/`*.db`、日志、会话/证据原始数据、个人聊天文本、截图/像素、临时 pending application、`.venv/`、`__pycache__/`、`.pytest_cache/`、`.hypothesis/`、`bin/`、`obj/`、`dist/`、`build/`。`.gitignore` 已将多数状态和密钥后缀列为排除项，但复制脚本仍应显式排除构建目录和诊断输出。

`fixtures/` 只可携带脱敏、非个人的离线测试夹具；不能把真实联系人证据或原始 UIA 数据混入包。`docs/`、`tests/`、`third_party/`可按审计/验证需要保留，但它们不是 guest 运行时依赖，不能以示例 capability、fixture 或文档中的历史事实替代当前认证。

## Guest 用户路径与配置建议

建议以专用、交互式登录且不启用自动登录的 Windows 用户运行 QQ 和 runtime，例如项目放在 `C:\PMAI\app`，数据放在 `C:\PMAI\data`，DPAPI vault 放在 `C:\PMAI\secrets`，配置放在 `C:\PMAI\runtime.json`。QQ 使用该同一用户的官方安装和用户 profile（通常由 QQ 自己管理于 `%LOCALAPPDATA%`/`%APPDATA%`，不要复制其他用户的 profile 或直接搬运登录状态）；不要把 QQ profile 放进部署包。当前 VM closeout 显示 autologon 已清除；应保持这一约束。guest 诊断脚本中列出的 `C:\Program Files\Tencent\QQNT\QQ.exe` 和 x86 候选仅是探测候选，不是已安装事实。

## 安装包与启动缺项

`[web,llm,qq-vm]` 覆盖 Starlette/Uvicorn、OpenAI client 和 `uiautomation`/`pywin32`，但不包含 Python 解释器、官方 QQ、VirtualBox/Guest Additions、真实 selector/binding/capability、DPAPI vault 或 active rules 数据。bootstrap 的 pip 安装默认需要 guest 可访问包源；离线安装必须另行准备并审计 wheel 源，但本评审不下载它们。

配置还缺少可直接运行所需的真实 `data_dir`、`secret_vault`、contacts/bindings、完整 selector 六项（`main_window`、`conversations`、`conversation_item`、`composer`、`send`、`bubbles`）、环境 fingerprint/QQ client version、以及 capability 证据。`capability` 必须明确 `execution_mode=guest_foreground`、guest send/verify supported、background send/verify unsupported 且 healthy；示例值不会通过校验。

## 部署后只读 smoke checks（先不联网、不发消息）

1. 在 guest 启动官方 QQ 并完成登录后，确认窗口唯一、最大化、版本/签名/窗口类/DPI/环境 fingerprint 与当前 profile 一致；确认 QQ 账号和目标会话由人工选择。不要把“已登录”当作发送能力认证。
2. 运行 `inspect_qq_guest_bindings.py --selector-pack <当前selector-pack> --output <脱敏证据路径>`。检查退出码、`read_only=true`、`send_attempted=false` 和 candidates；人工逐一确认候选后才创建 binding。该命令不得选择会话、编辑 composer、发送或读取原始聊天正文。
3. 在不启动 worker/API/WebUI 的前提下运行 `run_vm_runtime.py --config C:\PMAI\runtime.json --check`。成功输出只能证明配置、vault 解密、selector/capability 和 active rules 结构通过；它不证明 DeepSeek 网络调用、QQ 发送或联系人身份永久稳定。失败时保持联系人暂停，不用示例 capability 或昵称替代证据。
4. 检查 WebUI 若启动只绑定 loopback（默认 `127.0.0.1`），先只访问状态/配置页；确认多联系人均有独立 binding、规则状态、暂停状态和健康状态。不要提交 prepare/commit/send、不要输入草稿、不要调用模型 API；必要时保持 guest 网络断开以验证本地启动链，但应预期真正 DeepSeek 调用会失败关闭。
5. 最后记录当前 guest 的 QQ 版本、环境 fingerprint、selector 版本、Python 版本、依赖版本和 smoke 结果到新的实机记录；更新 `docs/IMPLEMENTATION_STATUS.md` 的 B1/B4/B7 当前状态，但保留其中 534 测试的历史结果，不改写为实机验收。旧的 `V5-OFFLINE-ACCEPTANCE.md` 和现有 VM closeout 只能按时间点引用，不能覆盖新状态。

本评审唯一写入文件；未接触 credential-state、API key、私聊正文或原始个人数据。
