"""Generate auditable coverage artifacts from the actual database."""
import html
import json
from pathlib import Path
from .qa import validate
from .store import now_utc

LABELS={'company_profiles':'公司档案','securities':'证券身份','identity_history':'历史名称/代码','calendar_sessions':'开闭市日历','security_status':'证券状态','market_rules':'历史交易规则','daily_bars':'原始日线','adjusted_bars':'派生复权日线','corporate_actions':'公司行为','financial_facts':'结构化财务事实','filings':'SEC披露索引','documents':'公告/管理层原文','guidance':'公司指引','analyst_estimates':'历史市场预期','earnings_actuals':'实际业绩摘录','event_calendar':'公司事件','benchmark_bars':'比较基准','relations':'产业链关系','industry_metrics':'行业指标','macro_observations':'宏观数据','policy_documents':'政策原文','news':'新闻','research_materials':'授权研究/人工材料'}
GAPS={
 'securities':'本地稳定证券ID需人工治理；CIK是发行人ID，不能自动代表证券。无完整上市、退市、证券类别和历史股本数据库。',
 'market_rules':'规则接口与生效日期模型已预留，未全量接入历史制度、停牌、限制、做空和交易规则。',
 'daily_bars':'缺 Alpaca 账户/数据授权。原始 OHLCV、真实成交额、退市历史行情和完整市场覆盖未取得。',
 'adjusted_bars':'计算器已实现并测试，实际原始日线未接通；不生成虚构复权样本。现金分红缺除息日或相邻前收盘时拒绝计算。',
 'corporate_actions':'仅选定公开样本；完整公司行为历史和复杂并购/分拆/退市现金流待供应商及授权。',
 'financial_facts':'SEC companyfacts 仅标准taxonomy且全发行人范围；原文保留附注，分部、自定义标签、完整报表展示与附注结构化尚未全覆盖。',
 'documents':'仅下载配置上限内原文和选定附件，其余索引保留并记录缺口；新闻稿、演示、电话会文本非全量。',
 'guidance':'样本摘录公司收入指引。网页回填版无法证明历史同版本，按首次获取可见。',
 'analyst_estimates':'无法研究相对市场预期的业绩差异。历史一致预期、分歧和修订待商业供应商授权。',
 'event_calendar':'披露日期与财报发布日分开；预计发布日期和后来实际日期未形成完整历史修订序列。',
 'benchmark_bars':'缺市场/行业基准授权行情和分红口径，不以当前指数成分替代历史成分。',
 'relations':'支持带证据、时间及确认程度的人工导入；不从叙述自动推断客户/供应商关系。',
 'industry_metrics':'指标与单位接口预留，未全面采集产品价格、库存、产能、订单和交期。',
 'macro_observations':'无 FRED API key 时仅公开 CSV 当前版本；不能将观测日期当作发布/历史vintage日期。',
 'news':'仅显式配置RSS/Atom等公开源；不能视为全网覆盖，跨来源同事件聚类需复核。',
 'research_materials':'仅允许有出处和授权声明的导入；调查必须注明样本和方法；推断保留方法与输入记录。'}


def write_reports(store,config):
    out=Path(config['_base_dir'])/'reports';out.mkdir(exist_ok=True)
    qa=validate(store,config)
    stats=[dict(r) for r in store.db.execute('''SELECT dataset,COUNT(*) versions,COUNT(DISTINCT source||':'||record_key) logical_keys,COUNT(DISTINCT company_id) companies,COUNT(DISTINCT security_id) securities,MIN(COALESCE(period_start,event_date,period_end)) period_start,MAX(COALESCE(period_end,event_date,period_start)) period_end,MIN(published_at) earliest_public_label,MAX(published_at) latest_public_label,MIN(retrieved_at) first_acquired,MAX(retrieved_at) last_acquired,SUM(available_at IS NULL) unknown_availability FROM records GROUP BY dataset''')]
    by={s['dataset']:s for s in stats}
    raw=store.db.execute('SELECT COUNT(*) n,SUM(byte_size) bytes FROM raw_blobs').fetchone()
    runs=[]
    for f in sorted((store.root/'runs').glob('*.json')) if (store.root/'runs').exists() else []:
        runs.append(json.loads(f.read_text(encoding='utf-8')))
    report={'generated_at':now_utc(),'market':config['market'],'sample_is_unbiased_universe':False,'raw_files':raw['n'],'raw_bytes':raw['bytes'] or 0,'datasets':stats,'gaps':GAPS,'quality':qa,'runs':runs}
    (out/'coverage.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'field_coverage.json').write_text(json.dumps(qa['field_coverage'],ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'quality.json').write_text(json.dumps({k:v for k,v in qa.items() if k!='field_coverage'},ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# 实际覆盖与缺口报告','',f"生成时间（UTC）：{report['generated_at']}",'',f"原始文件 {raw['n']} 个，共 {(raw['bytes'] or 0)/1024/1024:.2f} MiB。以下数量直接来自 SQLite，测试合成数据没有混入本库。",'',
           '本库是有目的的小样本，不能作为无偏回测股票池。尚未完成全部基础数据集接通；来源接通不等于历史可见状态已经可靠重建。','',
           '| 数据集 | 版本数 | 逻辑键数 | 公司数 | 证券数 | 描述期间/事件日期范围 | 未知可见时间 |','|---|---:|---:|---:|---:|---|---:|']
    for name,label in LABELS.items():
        r=by.get(name,{})
        lines.append(f"| {label} `{name}` | {r.get('versions',0)} | {r.get('logical_keys',0)} | {r.get('companies',0)} | {r.get('securities',0)} | {r.get('period_start') or '—'} ～ {r.get('period_end') or '—'} | {r.get('unknown_availability',0)} |")
    lines.extend(['','## 证券与采样范围','','| 公司ID | 证券ID | 配置代码 | 采集披露日期窗口 |','|---|---|---|---|'])
    for s in config.get('securities',[]): lines.append(f"| {s['company_id']} | {s['security_id']} | {s['symbol']} | {s.get('start',config['start'])} ～ {s.get('end',config['end'])} |")
    lines.extend(['','## 明确缺口',''])
    lines.extend(f'- **{LABELS[k]}**：{v}' for k,v in GAPS.items())
    lines.extend(['','## 时间与版本的解释','','- `published_at` 是来源标示日期或 SEC 受理代理时间；不等于系统拿到它的时间。','- `available_at` 必须结合 `availability_basis` 使用。SEC 日精度采用下一纽约日零点的保守假设，不能保证精确盘中历史。','- 当今网页、行情、当前宏观CSV和当前库生成的日历按首次观测可见；无法证明过去的相同版本。','- `as_of` 控制可见时间；`known_at` 额外控制系统当时是否已获取。只用一个时间轴不足以复现当时系统状态。','- 财务事实按 accession 留存；更正表单不自动代表所有数字更正。`financial-snapshot` 只比较相同指标、单位和完整期间。','- `raw_blobs`、`raw_retrievals`、`record_evidence` 和内容哈希提供原文与重复获取链。字段非空率见 `field_coverage.json`。','',f"质量检查：{qa['status']}；错误 {sum(x['severity']=='error' for x in qa['findings'])}，缺口/告警 {sum(x['severity']!='error' for x in qa['findings'])}。完整列表见 `quality.json`。"])
    (out/'COVERAGE.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    # Readable local report, with exact facts and no external JavaScript/assets.
    rows=''.join('<tr><td>'+html.escape(LABELS.get(s['dataset'],s['dataset']))+'</td><td>'+str(s['versions'])+'</td><td>'+str(s['logical_keys'])+'</td><td>'+html.escape(str(s['period_start'] or '—'))+' → '+html.escape(str(s['period_end'] or '—'))+'</td></tr>' for s in stats)
    gap_html=''.join('<li><b>'+html.escape(LABELS[k])+'</b> '+html.escape(v)+'</li>' for k,v in GAPS.items())
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>股票投研数据采集 · 交付报告</title><style>body{font:16px/1.7 system-ui,"Microsoft YaHei",sans-serif;margin:0;background:#f2f5f7;color:#163043}main{max-width:1080px;margin:40px auto;padding:38px;background:white;border-radius:14px}h1{font-size:32px}h2{margin-top:36px}table{width:100%;border-collapse:collapse;font-size:14px}td,th{padding:10px;border-bottom:1px solid #dbe3e8;text-align:left}a{color:#086c8b}.badge{padding:18px;border-left:5px solid #bf8a23;background:#fff7e5}.metrics{display:flex;gap:25px;font-size:21px;margin:24px 0}li{margin:12px 0}code{background:#eef2f5;padding:3px 5px}</style><main><p>US EQUITY / EVIDENCE FIRST</p><h1>股票投研数据采集系统</h1><div class="badge">公开来源小样本已落盘。行情、完整退市历史和历史一致预期等仍有明确依赖。本样本不可作为无偏回测股票池。</div>'''
    page+=f'<div class="metrics"><span>{raw["n"]} 个原始文件</span><span>{sum(s["versions"] for s in stats):,} 条标准化版本</span><span>{len(config.get("securities",[]))} 个样本证券</span></div>'
    page+='<p><a href="../README.md">运行说明</a> · <a href="../docs/SOURCES.md">来源清单</a> · <a href="../docs/FIELD_DICTIONARY.md">字段字典</a> · <a href="ACCEPTANCE.md">验收报告</a> · <a href="COVERAGE.md">完整覆盖报告</a></p><h2>实际数据覆盖</h2><table><tr><th>数据集</th><th>版本数</th><th>逻辑键数</th><th>报告期间范围</th></tr>'+rows+'</table><h2>历史查询的边界</h2><p>原文发布时间、保守可用时间、系统实际获取时间分别保存。用 <code>as_of</code> 过滤信息可见时间，用 <code>known_at</code> 重建系统当时已经取得的内容。当前网页副本、当前宏观快照以及日历库规则不伪装成过去版本。</p><h2>待补齐的数据与权限</h2><ul>'+gap_html+'</ul><p>生成时间（UTC）：'+html.escape(report['generated_at'])+'</p></main></html>'
    (out/'index.html').write_text(page,encoding='utf-8')
    return {'status':qa['status'],'report':str(out/'index.html'),'raw_files':raw['n'],'records':sum(s['versions'] for s in stats)}
