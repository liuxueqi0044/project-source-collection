# V5 竞争来源适配器与边界

`competition_web` 是独立来源，提供 `competition_snapshots`，复用 V4.1 的 HTTP 限速/预算、持久队列、原始响应、版本与重试。竞争分类和目标关联由独立分析队列处理；原文取得不代表事件提取或全网站覆盖完成。

## 支持与依赖

|能力|实现范围|未实现或限制|
|---|---|---|
|公开静态网页|配置入口、CSS正文/字段选择器、原文与规范化版本|登录页面、付费权限、地域浏览器交互不支持|
|RSS/Atom|保存完整feed原文；有界枚举原始条目链接|feed摘要不冒充新闻全文；过期条目无法追溯|
|网页新闻链接、sitemap|显式允许域、选择器或通用新闻/产品标签、可调深度和文档上限|被上限排除的URL进入gap，不能报完整；未列明资料没有全互联网分母|
|动态渲染|配置为非HTTP时给出能力缺口|未安装Playwright/Crawl4AI；不把JS壳页当已完成业务提取|
|SEC/官方IR|原有独立适配器及已有原文继续可用|本适配器不伪造证券ID，不把无CIK企业送入SEC|
|开放搜索、授权新闻全文|适配边界保留|未提供账号/授权，未接通|
|语义分析|规则模块独立消费snapshot|snapshot不是结构化事件或已核验关系|

每个endpoint保存稳定ID、主体ID、URL、允许域、官方/第三方校验、归属证据、语言/货币/登录状态、选择器版本、观察环境、检查频率及业务范围。配置URL本身不证明它属于某家公司。

## 规划与资源控制

`plan_watches(store, config, target_ids=None, now=None, force=False)` 只读数据库，不联网。输出冻结主体、竞争关系与入口版本、`all_endpoints`、本轮 `endpoints`/`due_endpoints`、排除原因与缺口。

默认单层、每目标5个core及10个watch、全局30个去重peer。多个目标共享同一对手入口只采一份，保留全部目标和关系版本关联。confirmed且产品/地区/客户群都已知的有效关系可进入core；业务明确但地区或客户未知仅可watch，并留下审核缺口。候选关系、业务未知、过期或尚未有效的关系不进入正常竞争监测。审核过期不等于关系已经结束，降为watch并标记复核。

核心入口按既有三时点检查；watch每日一次。到期字段是检查资格，实际执行依本机调度、执行锁和预算。首次网页只是当前基线。未知发布时间保留未知，当前页面不能被回填为历史页面。

链接枚举每个入口默认深度1、最多20份文档；可配置上限，硬上限深度3/1000份。每轮重新枚举本轮看到的清单；超限URL及定位保留在gap。队列预算中止保留未完成任务。恢复和下一轮正常刷新使用同一现有队列，不复制成功业务数据。

## URL与HTTP运输

只允许明确配置的HTTP(S)域及其子域，拒绝URL用户名/密码、localhost、私网/保留IP、非80/443端口。解析DNS后，任何非公网地址使请求拒绝；连接固定在已检验公网IP上，HTTPS SNI与证书仍验证原域名。默认不会使用环境代理或 `.netrc` 凭据。所有重定向在下一个请求前重新验证允许域、协议、DNS；拒绝HTTPS降级，每跳计请求、字节和限速。403不会通过浏览器、代理或改身份绕过。

本机发现系统DNS返回198.18.0.x（fake-IP）。默认安全检查因此拒绝采集，失败证据保留。需要适应该网络时，可以显式配置：

```toml
[competition.transport]
dns_mode = "cloudflare_doh"
```

此模式通过固定公网 `1.1.1.1` 建立到 `cloudflare-dns.com` 的TLS连接，验证该域证书，使用官方DoH JSON接口。回答中的目标地址必须是公网IP；随后固定连接目标IP并验证目标域TLS。不会把198.18地址列入允许范围，也不会在DoH失败后降级到私网连接。DNS答复原文保存为基础设施证据 `competition_dns`，每次DoH请求和字节计入竞争预算。按返回TTL缓存，最多300秒；IPv4无结果再请求IPv6。无需API key/新订阅。边界：信任Cloudflare HTTPS解析响应和本机CA/网络栈；不声称所有DNS记录均经DNSSEC验证。JSON响应格式不属于正式RFC。

参考：[Cloudflare官方DoH JSON文档](https://developers.cloudflare.com/1.1.1.1/encryption/dns-over-https/make-api-requests/dns-json/)。实际已保存Oracle新闻页、Siemens公司页及重定向/解析响应；详见 `reports/v5/public-dns-pinning-smoke.json`，这些是运输冒烟证据，不是事件语义验收。

## 状态与防止误报

`endpoint_receipts` 分开记录：入口本轮是否实际检查、入口规范化是否完整、枚举资料分母、成功检查与规范化数量、仍未完成的URL、预算中止和下一到期资格。仅入口返回200不足以推进整个endpoint范围成功；子文档失败、解析拒绝、枚举截断均保留缺口。另一URL获取了相同哈希，不能证明该入口本轮已检查。旧原文保留不等于当前刷新完成。

规范化结果仅写入独立 `competition_snapshots`；不调用原财务 `persist_observations`，因此不会用竞争输出撤销财务字段。同字节重复刷新增加获取流水与证据关联，保持snapshot版本幂等；正文噪声是否构成事件由后续变化模块判断。

## 依赖复用与验收

未新增Python依赖。使用已有锁定的 requests 2.34.2、beautifulsoup4 4.15.0、lxml 6.1.3；未安装额外采集服务或创建Windows任务。IP固定运输依赖已锁定requests的HTTPAdapter扩展接口，升级该依赖时需重跑证书域名/公网IP/重定向预算测试。

`tests/test_competition_adapter.py` 包含24项合成/受控测试：SSRF、混合DNS拒绝、TLS域名与IP固定、重定向预算与域限制、403、DoH缓存/私网答复拒绝、离线无DNS、快照幂等/财务隔离、RSS截断、单层范围/去重/cap、unknown范围watch、到期资格、入口成功子文档失败/恢复等。与旧IR/open-source回归合计45项通过，XML见 `reports/v5/adapter-regression.xml`。真实运输、合成故障测试与未来自然变化观察分别报告。
