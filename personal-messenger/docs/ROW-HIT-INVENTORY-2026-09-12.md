# 2026-09-12 会话行命中测试只读清单（坐标路线结论）

本文记录一次纯只读的 UIA 证据采集，用来回答「坐标单击能否成为 contact2 会话选择的等价身份证明」。
结论是**不能**，而且原因不是身份证明设计得不够巧，是 QQ 9.9.33 的 Chromium 窗口不把命中点解析进
承载会话行的可访问性子树。同时本次顺带查明了四次动态坐标诊断里 `HEADER_PROBE_FAILED` 的真实原因，
并发现当前 QQ 客户端只显示联系人列表、没有任何会话处于打开状态。

本文不改变任何游标、消息、审计或发送操作。本轮 `receipt count` 仍为 0，没有任何鼠标或键盘输入。

## 1. 新增工具与本轮执行

| 文件 | 作用 |
| --- | --- |
| `outputs\qq-vm\install\diagnose_qq_row_hit_inventory_guest.py` | guest 端只读探测：原始行属性、selector 普查、header 探查子原因、9 点命中祖先链 |
| `outputs\qq-vm\install\diagnose_qq_row_hit_inventory_host.py` | 宿主端运行器：暂存、执行一次、回传并校验；含只读守卫 |
| `outputs\qq-vm\install\selftest_qq_row_hit_inventory.py` | 离线自检，7/7 通过（含只读守卫的正反用例） |

SHA256：

```text
06E560526CDAA725C5C463084369E69F50BBB788F7DCEA64C7D0AB2C96019704  diagnose_qq_row_hit_inventory_guest.py
B94FE51B64F0D0A30EB3C6E18D8707F97D4A43B9FA8803A54D1CB9ACA4DD6BC9  diagnose_qq_row_hit_inventory_host.py
2990DFE5A46DDA7C6F982062B80509CE11133215A4DBCE8C0A3597BB427B24AF  selftest_qq_row_hit_inventory.py
```

只读保证：

- guest 脚本不 import `SendInput`／`mouse_event`／`keybd_event`，不调用任何 UIA 动作模式，不写输入框、不移动焦点、不选择行。
- 宿主运行器在暂存前扫描脚本正文，出现输入或动作 API 即拒绝运行；自检里有正反两个用例。
- 运行前宿主运行器要求 `runtime_process_alive is False`。本轮运行期间完整 runtime 仍为 `graceful_stopped`。
- 两次运行耗时 `9694ms`、`8880ms`，`mouse_*` 一类计数在脚本里根本不存在，因为没有任何输入代码。

证据（均在 `outputs\qq-vm\install\diagnostics\`）：

```text
qq-row-hit-inventory-r20260912-17-e0c68054d4e5472dad19982bf05c2e3b.json
qq-row-hit-inventory-r20260912-17-83fcc7e48e91473bb8cb73dbf4eb9b12.json
```

两次运行相隔约一分钟，结果逐字段一致：同一 QQ `PID 10596`、`HWND 197750`、同一目标 runtime id
`42/132268/4/5/1/41`、同一行矩形 `(56,732)-(306,796)`、18 行、9 个命中点全部不匹配。

## 2. 结论一：没有任何会话行暴露 UIA `Name`

18 行全部 `name_is_empty=true`、`name_length=0`、`name_sha256_12=null`，class 一律
`recent-contact-item`，ControlType 一律 `GroupControl`。

这意味着 `diagnose_qq_dynamic_click_once_guest.py:132` 的
`ROW_VISUAL_IDENTITY_INVALID` 门不是 contact2 的特例，而是对这张联系人列表**任何一行都不可能通过**。
交接文档里「目标 row 本身没有可用于视觉身份的 UIA Name」应更正为「该 QQ 版本的所有会话行都没有 UIA Name」。

目标行的可用模式只有 `invokepattern`、`legacyiaccessiblepattern`、`scrollitempattern`；
**没有 `selectionitempattern`**，所以 `Select()` 路线不存在。`LegacyIAccessiblePattern.DefaultAction`
仍是 3 字节、SHA256 `eea50f41…06f6`，与生产常量一致。

## 3. 结论二：`ControlFromPoint` 不进入承载会话行的提供者

9 个网格点全部 `inside_row=true`、`chains=9`、`hit_point_row_match_mode=null`。
每点的祖先链形状完全一致，最深处是整窗尺寸的通用面板，而不是行或行的子元素：

```text
type=PaneControl     class='View'                 rect=0,0,2560,1381   runtime=42/197750/4/0/0/26
type=PaneControl     class='View'                 rect=0,0,2560,1381   runtime=42/197750/4/0/0/19
type=PaneControl     class='View'                 rect=0,0,2560,1381   runtime=42/197750/4/0/0/18
type=PaneControl     class='ClientView'           rect=0,0,2560,1381   runtime=42/197750/4/0/0/22
type=PaneControl     class='View'                 rect=0,0,2560,1381   runtime=42/197750/4/0/0/51
type=PaneControl     class='ClientView'           rect=0,0,2560,1381   runtime=42/197750/4/0/0/23
type=PaneControl     class='View'                 rect=0,0,2560,1381   runtime=42/197750/4/0/0/20
type=WindowControl   class='Chrome_WidgetWin_1'   rect=-8,-8,2568,1389 runtime=42/197750
type=PaneControl     class='View'                 rect=0,0,2560,1381   runtime=42/65548
```

目标行的 runtime id 属于提供者 `132268`，而命中点整条链属于提供者 `197750` 与 `65548`。
两者不是「层级不够深」的问题：链只有 9 层就终止在窗口根，与行所在的子树没有任何祖先关系。
`WindowFromPoint` 同时报告 `root_hwnd=197750`、`root_process_id=10596`，说明坐标本身完全正确、确实落在 QQ 窗口内。

因此 `UIA_POINT_TARGET_MISMATCH` 不是可以靠放宽身份规则解决的缺陷。**坐标单击路线应当停止**，
不要继续增加 hit-test 约束、ancestor geometry 或视觉等价身份；这条路上没有可用的命中结果可供证明。

## 4. 结论三：四次 `HEADER_PROBE_FAILED` 是诊断自身缺陷，不是选择结果

`header_probe` 逐级复现 `session_identity._header_digest` 的筛选，并记录每一步的计数：

```text
root_rect_valid=true, composer_match_count=0, composer_rect_valid=false,
failure_reason="composer_not_unique"
```

`_header_digest` 在 `composer_match_count != 1` 时立刻抛错，而
`diagnose_qq_dynamic_click_confirmation_guest.py` 用 `raise DiagnosticError("HEADER_PROBE_FAILED") from exc`
把 `header_not_unique`、`composer_not_unique`、`composer_geometry_invalid`、`header_geometry_invalid`
全部折叠成一个码。四次运行记录到的 `descendant_count=0`、`class_token_count=0` 等字段全部是提前返回时的默认值，
不是测量结果 —— 这一点也说明该脚本的布尔字段在没有走完流程时不可当作证据。

这与 `bridge._prepare_send_locked` 折叠 worker 错误是同一类缺陷：把「未确认」写成了没有可判定信息的结论。

## 5. 结论四：当前 QQ 只显示联系人列表，没有任何会话打开

同类普查给出：

```text
conversations=18, conversation_item=18, composer=0, send=0, bubbles=0
descendant_count=319, descendant_scan_truncated=false, editor_candidates=0
```

319 个后代的 class 直方图只包含 `recent-contact-item`、`item__avatar`、`item__info`、`item__content`、
`nav-item`、`contact-adder-btn`、`draggable-view__container empty-panel_head` 这类列表与导航结构，
完全没有 `ProseMirror`、`ExEditor-qq-msg-editor`、`ml-root` 或 `send-msg`。

也就是说：聊天面板当前没有打开。这一状态此前没有被记录，而且它同时解释了全部四次
`HEADER_PROBE_FAILED`。下一次会话选择验收必须先把这一点写进现场基线，否则「选择后未确认」无法与
「本来就没有会话打开」区分开。

## 6. 对交接文档第 10 节两处表述的更正

1. 交接文档与前一版 selector 子代理都记录「后三次运行 guest 前台漂移」。这是误读：
   `02f91a88`、`0040ef0b`、`8dfb6ff9` 三次根本没有走到赋值的代码位置，
   `foreground_match=false`、`point_inside_row=false`、`hit_candidates_checked=0` 都是初始化默认值。
   唯一的真实前台观测是 `a836138f` 的 `foreground_match=true`；本轮两次运行也确认
   `GetForegroundWindow()==197750`。没有证据表明前台会漂移。
2. 交接文档把坐标路线的判据描述为「唯一 exact row 的 class/control type/BoundingRectangle + 唯一父列表矩形
   + point ancestor geometry + QQ root HWND/PID」。本节证据表明该等价身份**无法构造**：
   命中点根本不返回行所在子树的任何元素，而行本身也没有 `Name` 可用于视觉回退。

## 7. 建议的下一步

1. 先把 QQ 的现场状态（聊天面板是否打开、哪一行处于选中态）写成每次 selector 验收的前置条件记录，
   再谈选择动作是否成功。
2. 修正动态坐标诊断的确认阶段：先 `ensure_guest_foreground`，进 `read_phase` 后先确认
   `composer_match_count == 1`，并保留 `_header_digest` 的具体子原因，不要折叠成 `HEADER_PROBE_FAILED`。
   在这一步修好之前，该诊断的成功路径在结构上不可达。
3. 会话选择不要再走坐标。行本身有 `InvokePattern` 与 `LegacyIAccessiblePattern`，
   而 r16 已证明 `DoDefaultAction` 对 contact1 生效、对 contact2 不生效；生产当前刻意只用 Legacy。
   下一步应当是一次选择专用的隔离诊断，比较两条动作各自对 contact2 的效果，
   并且每次动作后由不重叠的新进程确认 selected token + header identity + group marker 0。
4. 本轮没有做任何动作、没有发送、没有改游标；`qq_vm_ops` 与 receipt 计数不受影响。
