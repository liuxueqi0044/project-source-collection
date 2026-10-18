"""Validate delivered REAL data; synthetic failure cases live only in tests/."""
import json
import sys
import hashlib
from pathlib import Path
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
from equity_data.store import Store
from equity_data.sec import financial_snapshot
from equity_data.qa import validate
from equity_data.cli import load_config


def main():
    config=load_config(BASE/'configs/us.sample.toml')
    results=[]
    def record(name,ok,detail,ids=None,status=None):
        results.append({'name':name,'status':status or ('passed' if ok else 'failed'),'detail':detail,'record_ids':ids or []})
    with Store(config['storage']) as store:
        qa=validate(store,config)
        record('原文哈希与外键完整性',not any(x['severity']=='error' for x in qa['findings']),
               '逐个原始文件校验 SHA256，PRAGMA foreign_key_check；内容检查不等于授权或历史版本真实性证明。')
        identity=store.query('identity_history',security_id='local:us:meta-class-a')
        changed=[r for r in identity if r['data'].get('old_symbol')=='FB' and r['data'].get('new_symbol')=='META' and r['data'].get('valid_from')=='2022-06-09']
        record('更名及代码变更证据',bool(changed),'Meta FB→META 仍关联同一 local:us:meta-class-a；来源为Nasdaq通知。当前网页副本按获取时可见，未证明历史完整股票池。',[r['record_id'] for r in changed])
        statuses=store.query('security_status',security_id='local:us:twtr-common')
        state={r['data']['status']:r for r in statuses}
        good=state.get('trading_suspended',{}).get('data',{}).get('valid_from')=='2022-10-28' and state.get('delisting_announced',{}).get('data',{}).get('valid_from')=='2022-11-08'
        record('退市证券保留与日期区分',good,'Twitter的2022-10-28停牌、2022-11-08通知中拟移除日期分别保留；后者不冒称已独立核验最终法律生效。',[r['record_id'] for r in statuses])
        actions=store.query('corporate_actions',security_id='local:us:nvda-common')
        split=[r for r in actions if r['data'].get('action_type')=='split']
        div=[r for r in actions if r['data'].get('action_type')=='cash_dividend']
        record('拆股日期和比例',bool(split) and split[0]['data']['split_ratio']==10 and split[0]['data']['ex_date']=='2024-06-10','NVIDIA 10:1；2024-06-07分发/生效与2024-06-10按拆股后价格交易分开。',[r['record_id'] for r in split])
        record('分红缺失不猜测',bool(div) and div[0]['data']['cash_amount']==.01 and div[0]['data']['ex_date'] is None,'来源有拆股后每股0.01USD及支付日；没有明确除息日的字段保持null。实际总收益复权尚未验收。',[r['record_id'] for r in div])
        before=financial_snapshot(store,'sec:0001045810','2024-06-01')['records']
        after=financial_snapshot(store,'sec:0001045810','2025-06-01')['records']
        def eps(rows):
            return [r for r in rows if r['data']['concept']=='EarningsPerShareDiluted' and r['period_start']=='2024-01-29' and r['period_end']=='2024-04-28' and r['unit']=='USD/shares']
        old,new=eps(before),eps(after)
        record('真实历史EPS回列与未来信息隔离',bool(old and new) and old[0]['data']['value']==5.98 and new[0]['data']['value']==.60,
               f"同一季度2024披露EPS={old[0]['data']['value'] if old else 'missing'}；2025后续比较数EPS={new[0]['data']['value'] if new else 'missing'}。这是拆股口径回列，不是财务错误更正。",
               [r['record_id'] for r in old+new])
        known=store.query('financial_facts',as_of='2024-06-01',known_at='2024-06-01',company_id='sec:0001045810')
        record('历史回填获取时间不伪造',not known,'2026获取的旧文件，在known_at=2024-06-01下返回0条。')
        filings=store.query('filings',company_id='sec:0001045810')
        target=[r for r in filings if r['data']['accession']=='0001045810-24-000113']
        if target:
            r=target[0]
            accepted=datetime.fromisoformat(r['published_at'].replace('Z','+00:00')).astimezone(ZoneInfo('America/New_York'))
            early=store.query('filings',company_id='sec:0001045810',as_of='2024-05-22T16:00:00-04:00')
            absent=all(x['data']['accession']!='0001045810-24-000113' for x in early)
            nextday=store.query('filings',company_id='sec:0001045810',as_of='2024-05-23T00:00:01-04:00')
            present=any(x['data']['accession']=='0001045810-24-000113' for x in nextday)
            record('盘后公告的保守日界线',accepted.hour>=16 and absent and present,
                   f"SEC原始header接受时间 {accepted.isoformat()}；16:00查询不含此公告，次日才可见。仅验证保守规则，不声称接受时刻=确切公开时刻。",[r['record_id']])
        else: record('盘后公告的保守日界线',False,'未取得指定公告。')
        facts=store.query('financial_facts',company_id='sec:0001045810')
        revenue=[r for r in facts if r['data']['concept']=='Revenues' and r['period_start']=='2024-01-29' and r['period_end']=='2024-04-28' and r['data']['accession']=='0001045810-24-000124']
        actual=store.query('earnings_actuals',company_id='sec:0001045810')
        amount_ok=bool(revenue and actual) and revenue[0]['data']['value']==26044000000 and actual[0]['data']['original_value']==26044 and actual[0]['data']['scale']==1000000
        record('不同金额单位核对',amount_ok,'SEC USD数值26,044,000,000，对照新闻稿26,044百万USD；API值不再次乘百万。EPS的USD/shares和股数shares另存。',[r['record_id'] for r in revenue+actual])
        record('财报错误更正/10-K或10-Q修订',False,'版本机制由隔离合成测试验证；本次没有独立完成真实财务错误更正前后原文数字对账。不能把拆股回列或任意/A表单当作已完成此项。',status='not_source_validated')
        record('真实行情及复权链路',False,'没有授权原始日线。拆股/分红算法及跨来源重复拒绝通过合成测试，真实价格复权验收仍缺原始行情和完整公司行为。',status='blocked_dependency')
        record('历史市场预期',False,'未取得带历史时点的一致预期，无法研究相对市场预期的业绩差异。',status='blocked_dependency')
    output={'generated_at':datetime.now(timezone.utc).isoformat(),'scope':'Real samples only; cannot certify unbiased backtest readiness','results':results}
    out=BASE/'reports';out.mkdir(exist_ok=True)
    (out/'sample_acceptance.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# 验收报告','','这是公开来源小样本验收，未满足完整回测数据验收。通过项只覆盖所述事实与软件行为，依赖阻断和未核验项不是通过。','',f"生成时间（UTC）：{output['generated_at']}",'','## 真实资料抽查','','| 检查 | 结果 | 证据与边界 |','|---|---|---|']
    for r in results:lines.append('| '+r['name']+' | '+r['status']+' | '+r['detail']+' |')
    lines+=['','每项对应的标准化record_id见 `sample_acceptance.json`；运行 `python -m equity_data trace <record_id>` 可查看原文URL、定位、SHA256、获取记录、发布时间、可用时间依据及修订链。','',
            '## 软件验收与真实数据的区别','','测试使用临时目录的合成输入，覆盖日期/时区/盘后边界、版本保留、A→B→A、显式历史版本重放、幂等、并发、重试限速、密钥脱敏、文件错误识别、价格异常、跨来源冲突、量额单位、复权、人工导入声明。实际执行结果保存在 `pytest.xml`。合成测试成功不等于对应供应商真实历史覆盖验收。','',
            '实际原文的离线重复解析检查见 `idempotence.json`。该检查只验证同一输入重复处理，不代表供应商永不更正。','',
            '## 对四个问题的回答','','1. **数字来自哪里？** record → record_evidence → raw_retrievals/raw_blobs，连到完整原件、内容哈希及原文位置。','2. **当时是否已公开？** 依据 published_at、publication_precision、publication_timezone、available_at和availability_basis。精确证据缺失则采用明示假设或首次获取时间；无法可靠重建盘中状态时明确限制。','3. **后来是否修改？** 查询全部版本和不同accession，比较相同指标/单位/期间。系统保存修订链；首次抓取前发生但未保留的网页修改无法恢复。','4. **历史查询是否读入未来？** as_of 和 known_at 双重过滤，未注明时刻的冲突版本保守处理。当前Companyfacts的历史数据抽取可能有后续处理，未逐项核对原始iXBRL的数值不能宣称完全证明as-filed状态。','',
            '## 尚未验收完成','','完整上市退市历史股票池；全部市场规则与停复牌；授权原始行情/成交额/基准及退市回报；全量财报附注/分部结构化；实际财务错误更正；历史ALFRED vintages（无key时）；公司指引/新闻修改的完整版本史；历史市场一致预期。扩容前应按依赖清单逐一取得来源并复验。']
    (out/'ACCEPTANCE.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    if (out/'pytest.xml').exists():
        junit=ET.parse(out/'pytest.xml').getroot()
        suites=list(junit.iter('testsuite'))
        total=sum(int(s.get('tests',0)) for s in suites)
        failed=sum(int(s.get('failures',0))+int(s.get('errors',0)) for s in suites)
        skipped=sum(int(s.get('skipped',0)) for s in suites)
        with (out/'ACCEPTANCE.md').open('a',encoding='utf-8') as f:
            f.write(f'\n本次实际软件测试：{total} 项，失败/错误 {failed}，跳过 {skipped}。真实资料检查 {sum(r["status"]=="passed" for r in results)} 项通过；其余按上表明确未核验或依赖阻断。\n')
    print(json.dumps({'checks':len(results),'passed':sum(r['status']=='passed' for r in results),'failed':sum(r['status']=='failed' for r in results),'not_source_validated':sum(r['status']=='not_source_validated' for r in results),'blocked':sum(r['status']=='blocked_dependency' for r in results)},ensure_ascii=False))
    return 1 if any(r['status']=='failed' for r in results) else 0


if __name__=='__main__':raise SystemExit(main())
