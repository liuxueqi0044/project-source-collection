"""Machine-readable coverage plus a reviewable report, without inflated claims."""
from __future__ import annotations

import csv
import html
import json
from collections import Counter
from pathlib import Path

from .engine import status_report
from .store import now_utc
from .coverage import freshness_matrix


STATE_LABELS = {
    "discovered_not_downloaded":"已发现，尚未下载", "download_failed":"下载失败",
    "downloaded_unparsed":"已取得原文，尚未解析或解析失败", "parsed":"已解析（不等于实值已核验）",
    "partial_parse":"部分字段解析失败或不支持", "not_disclosed":"来源未披露",
    "not_applicable":"不适用", "missing_authorization":"缺少权限或授权", "source_conflict":"来源冲突",
    "pending":"等待执行", "running":"执行中", "succeeded":"任务执行结束",
    "failed":"失败，可续跑", "retry_wait":"等待重试", "blocked":"缺少依赖或适配能力",
    "deferred":"预算不足，已保留任务",
    "exhausted":"本轮资源重试预算耗尽，需显式重置",
}


def _registry(registry):
    if registry is None:
        path=Path(__file__).resolve().parents[1]/"configs"/"source_registry.json"
        registry=json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    elif isinstance(registry,(str,Path)):
        registry=json.loads(Path(registry).read_text(encoding="utf-8"))
    if isinstance(registry,dict):
        rows=registry.get("sources",registry)
        if isinstance(rows,dict):
            return [dict(value,source_id=value.get("source_id",key)) for key,value in rows.items() if isinstance(value,dict)]
        return rows if isinstance(rows,list) else []
    return list(registry)


def _record_statistics(store):
    result={"version_count":0,"latest_semantic_records":0,"raw_blob_count":0,"raw_bytes":0,"retrieval_count":0,
            "datasets":{},"sources":{},"companies":[],"quality":{},"knowledge_types":{},
            "extraction_modes":{},"public_time_unknown":0,"source_locator_missing":0,"content_evidence_link_missing":0,
            "period_start_min":None,"period_end_max":None,"published_at_min":None,"published_at_max":None}
    totals=store.db.execute("SELECT COUNT(*),COALESCE(SUM(byte_size),0) FROM raw_blobs").fetchone()
    result.update(raw_blob_count=totals[0],raw_bytes=totals[1],retrieval_count=store.db.execute("SELECT COUNT(*) FROM raw_retrievals").fetchone()[0])
    rows=store.db.execute("SELECT dataset,source,record_key,company_id,available_at,source_locator,quality,knowledge_type,data_json,period_start,period_end,published_at,raw_id,retrieval_id FROM records").fetchall()
    datasets,sources,quality,knowledge,modes=Counter(),Counter(),Counter(),Counter(),Counter()
    unique,companies,starts,ends,published=set(),set(),[],[],[]
    for row in rows:
        result["version_count"]+=1
        datasets[row["dataset"]]+=1
        sources[row["source"]]+=1
        quality[row["quality"]]+=1
        knowledge[row["knowledge_type"]]+=1
        data=json.loads(row["data_json"])
        mode=data.get("extraction_mode")
        if not mode:
            mode="manual_or_imported" if row["quality"].startswith("manual") else "not_labeled"
        modes[mode]+=1
        unique.add((row["dataset"],row["source"],row["record_key"]))
        if row["company_id"]:companies.add(row["company_id"])
        if not row["available_at"]:result["public_time_unknown"]+=1
        if not row["source_locator"]:result["source_locator_missing"]+=1
        if not row["raw_id"] or not row["retrieval_id"]:result["content_evidence_link_missing"]+=1
        if row["period_start"]:starts.append(row["period_start"])
        if row["period_end"]:ends.append(row["period_end"])
        if row["published_at"]:published.append(row["published_at"])
    result.update(latest_semantic_records=len(unique),datasets=dict(datasets),sources=dict(sources),companies=sorted(companies),
                  quality=dict(quality),knowledge_types=dict(knowledge),extraction_modes=dict(modes),
                  period_start_min=min(starts) if starts else None,period_end_max=max(ends) if ends else None,
                  published_at_min=min(published) if published else None,published_at_max=max(published) if published else None)
    result["note"]="记录统计包含所有版本；自动/人工分组仅依据记录明确标签，未标记记录不计入自动核验率。获取时间不是公开发布时间。"
    return result


def _frontier(report):
    tasks=report["tasks"]
    discoveries=[t for t in tasks if t["task_kind"]=="discover"]
    unclosed=[t["task_id"] for t in tasks if t["state"]!="succeeded"]
    incomplete=[t["task_id"] for t in discoveries if t["state"]!="succeeded" or not (t.get("result") or {}).get("enumeration_complete")]
    gaps=[g["gap_id"] for g in report["gaps"] if g["code"] in {"ENUMERATION_INCOMPLETE","SOURCE_ADAPTER_UNAVAILABLE","UNIVERSE_EMPTY","DATA_TYPE_NOT_SUPPORTED","CAPABILITY_UNAVAILABLE"}]
    return {"discovery_tasks":len(discoveries),"unclosed_task_ids":unclosed,"incomplete_discovery_task_ids":incomplete,
            "enumeration_gap_ids":gaps,"all_discovered_tasks_executed":bool(discoveries) and not unclosed,
            "discovery_frontier_closed":bool(discoveries) and not unclosed and not incomplete and not gaps,
            "source_coverage_complete":False,
            "interpretation":"发现根节点完成后，清单页/索引页仍可能继续产生子任务。所有已发现任务执行结束只说明该适配器遍历结束，不证明网站披露完整、历史版本齐全或字段全部正确。"}


def _acceptance(value):
    value=dict(value or {})
    groups={"software_tests":"软件测试", "real_field_accuracy":"真实字段准确性", "document_completeness":"资料完整性", "generalization":"渠道与解析泛化"}
    return {key:{"label":label,"result":value.get(key,{"status":"not_provided","note":"未提供独立验收证据，不能从任务完成数推导通过。"})} for key,label in groups.items()} | {"additional_evidence":{k:v for k,v in value.items() if k not in groups}}


def _cell(value):
    if value is None:return "未知"
    if isinstance(value,(dict,list,tuple)):value=json.dumps(value,ensure_ascii=False)
    return str(value).replace("|","\\|").replace("\n"," ")


def _table(headers,rows):
    return "| "+" | ".join(headers)+" |\n| "+" | ".join("---" for _ in headers)+" |\n"+"\n".join("| "+" | ".join(_cell(v) for v in row)+" |" for row in rows)+"\n"


def _write_csv(path,rows,fields):
    with path.open("w",encoding="utf-8-sig",newline="") as handle:
        writer=csv.DictWriter(handle,fieldnames=fields,extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key:json.dumps(value,ensure_ascii=False) if isinstance(value,(dict,list)) else value for key,value in row.items() if key in fields})


def generate_machine_reports(store, output_dir, registry=None, acceptance=None):
    output=Path(output_dir).resolve()
    output.mkdir(parents=True,exist_ok=True)
    report=status_report(store)
    report.update(source_registry=_registry(registry),record_statistics=_record_statistics(store),
                  enumeration=_frontier(report),acceptance=_acceptance(acceptance))
    report["company_type_freshness"]=freshness_matrix(report["resources"],store)
    report["reference_coverage"]={"status":"not_supplied","denominator":None,"coverage_rate":None,
        "instruction":"Run coverage --reference a frozen independent reference manifest; inventory counts are not coverage denominators."}
    report["report_schema_version"]="machine-report-4.0"
    paths={"json":output/"machine-status.json","markdown":output/"machine-report.md","html":output/"machine-report.html",
           "inventory_csv":output/"resource-inventory.csv","tasks_csv":output/"task-status.csv","scope_checks_csv":output/"source-scope-checks.csv"}
    paths["json"].write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    _write_csv(paths["inventory_csv"],report["resources"],["resource_id","source_id","resource_key","kind","data_type","url","company_id","security_id","state","raw_state","refresh_state","parse_state","upstream_state","last_refresh_attempt_at","last_refresh_success_at","last_parsed_at","failure_class","first_discovered_at","last_discovered_at","last_attempt_at","last_success_at","raw_id","retrieval_id","last_error","metadata"])
    _write_csv(paths["tasks_csv"],report["tasks"],["task_id","source_id","task_kind","resource_id","generation","state","attempt_count","created_at","updated_at","next_attempt_at","raw_id","retrieval_id","last_error","scope","result"])
    _write_csv(paths["scope_checks_csv"],report["source_scope_checks"],["scope_id","source_id","scope","last_attempt_at","last_discovery_success_at","last_acquisition_success_at","last_parse_success_at","last_complete_check_at","last_run_id","current_status","progress"])
    summary,stats=report["summary"],report["record_statistics"]
    lines=["# 金融信息获取机器：运行与覆盖报告","",f"生成时间：{report['generated_at']}（UTC）","",
           "本报告区分渠道能力、任务执行、原文取得、字段解析和独立核验。测试证券只用于证明采集能力。", "",
           f"已发现资源 {summary['resource_count']} 项；任务 {summary['task_count']} 项；未解决缺口 {summary['unresolved_gaps']} 项。", "",
           "## 资料清单与执行状态","",_table(["资源状态","数量"],[(STATE_LABELS.get(k,k),v) for k,v in summary["resource_states"].items()]),"",
           _table(["任务状态","数量"],[(STATE_LABELS.get(k,k),v) for k,v in summary["task_states"].items()]),"",
           f"发现过程是否完成遍历：{'是（仅限已配置适配器遍历）' if report['enumeration']['discovery_frontier_closed'] else '否或无法核实'}。", "",
           report["enumeration"]["interpretation"],"",
           "**不能把截断、缺权限、部分解析或下载失败计为完整覆盖。完整资源与任务清单保存在同目录 CSV 与 JSON，报告中的摘要不删除任何采集任务。**","",
           "## 来源健康与数据时效","",
           _table(["来源","运行状态","最后成功执行","最后获取原文","原文年龄/秒","仍未完成任务"],[(s["source_id"],s["status"],s["last_success_at"],s.get("latest_acquired_at"),s.get("acquisition_age_seconds"),s["open_task_states"]) for s in report["source_health"]]),"",
           "最后成功执行可能只是发现或解析成功，不能证明该来源的最新披露均已取得。原文年龄从实际获取时间计算，不能替代内容的发布日期或指标观测期。","",
           "## 精确范围的检查与多轴状态","",
           _table(["来源 / 范围","本轮状态","发现检查","获取检查","解析完成","全部检查"],[(s["source_id"]+" / "+s["scope_id"],s["current_status"],s["last_discovery_success_at"],s["last_acquisition_success_at"],s["last_parse_success_at"],s["last_complete_check_at"]) for s in report["source_scope_checks"]]),"",
           "以上时间仅适用于该scope保存的市场、股票池、日期、类型与来源配置；空值表示尚无成功证据。既有原文、当前刷新、当前原文解析和上游确认分别保存在raw_state、refresh_state、parse_state、upstream_state；下载失败不会抹掉旧原文。by_data_type分别列出获取与解析状态，语义候选/拒绝不得当成已核验财务事实。历史范围必须显式resume，不能由新的滚动窗口替代。", "",
           "更新轮次复用同一资源工作项，需求与失败尝试另行追加。认证/权限默认一小时冷却，限流及网络错误按配置退避；冷却中的任务仍报告未完成。machine-status.json包含work_items和task_scope_demands。", "",
           "## 当前预算与断点","",
           "```json",json.dumps((report.get("latest_run") or {}).get("summary"),ensure_ascii=False,indent=2),"```","",
           "## 实际记录与时间证据","",
           _table(["指标","数量或范围"],[("记录版本总数",stats["version_count"]),("不同语义记录键",stats["latest_semantic_records"]),("原始内容哈希数",stats["raw_blob_count"]),("原始字节数",stats["raw_bytes"]),("获取记录数",stats["retrieval_count"]),("未知可用时间的记录版本",stats["public_time_unknown"]),("缺少原文定位的记录版本",stats["source_locator_missing"]),("报告期间范围",[stats["period_start_min"],stats["period_end_max"]]),("公开发布时间范围",[stats["published_at_min"],stats["published_at_max"]]),("证券公司ID",stats["companies"])]),"",
           _table(["数据集","记录版本数"],stats["datasets"].items()),"",
           _table(["提取方式标签","记录版本数"],stats["extraction_modes"].items()),"",stats["note"],"",
           "历史查询应使用数据库的 as_of 与 known_at 查询。当前网页的新内容版本不能继承旧网页声称的历史可用时间。对于未知或不足的时间证据，无法可靠重建历史可见状态。","",
           "## 来源注册表与依赖","",
           _table(["来源","登记状态","市场","数据类型","认证/付费/依赖","已知限制"],[(s.get("source_id",s.get("id")),s.get("status"),s.get("markets"),s.get("data_types",s.get("datasets")),s.get("dependencies",s.get("authentication",s.get("auth"))),s.get("limitations",s.get("known_limitations"))) for s in report["source_registry"]]),"",
           "登记为已实现不等于真实接通；真实接通不等于所有字段已核验。商业预期数据无授权时，不能研究相对市场一致预期的业绩差异。","",
           "## 未解决缺口","",
           _table(["来源","公司","数据类型","状态/代码","说明"],[(g["source_id"],g["company_id"],g["data_type"],g["status"]+" / "+g["code"],g["message"]) for g in report["gaps"][:100]]),"",
           f"本节显示前 {min(100,len(report['gaps']))} 项；全部 {len(report['gaps'])} 项和每项结构化证据均在 machine-status.json。", "",
           "## 分项验收","" ]
    for item in report["acceptance"].values():
        if isinstance(item,dict) and "label" in item:
            lines += ["### "+item["label"],"","```json",json.dumps(item["result"],ensure_ascii=False,indent=2),"```",""]
    lines += ["软件测试通过不能替代实值对照；实值抽查通过不能证明资料完整；样本完整不能证明全市场或退市股票池覆盖。",""]
    markdown="\n".join(lines)
    paths["markdown"].write_text(markdown,encoding="utf-8")
    # A self-contained readable viewer intentionally displays the exact Markdown
    # text; no external script or CDN is needed to inspect local evidence.
    markup="<!doctype html><html lang='zh-CN'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>金融信息获取机器报告</title><style>body{font-family:system-ui,'Microsoft YaHei',sans-serif;background:#f4f6f8;color:#14212e;margin:0}main{max-width:1250px;margin:2rem auto;padding:2rem;background:white}h1{font-size:26px}nav a{margin-right:20px;color:#0969a2}pre{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.65;font:14px ui-monospace,monospace}</style><main><h1>金融信息获取机器：证据与覆盖</h1><nav><a href='machine-status.json'>全部结构化状态</a><a href='resource-inventory.csv'>资源清单</a><a href='task-status.csv'>任务清单</a><a href='machine-report.md'>Markdown</a></nav><pre>"+html.escape(markdown)+"</pre></main></html>"
    paths["html"].write_text(markup,encoding="utf-8")
    return {key:str(path) for key,path in paths.items()}
