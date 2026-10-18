"""Read-only, independent numeric evidence audit. This script never fetches data.

Use: python scripts/audit_machine.py --storage data/development --out reports/audit
The collector's numeric parsers are deliberately not imported. BeautifulSoup's
html.parser and Decimal independently reread saved source bytes. Store.query is
used only for explicitly labelled point-in-time boundary checks on a read-only
connection; the audit neither migrates nor modifies the inspected database.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import lru_cache
import hashlib
import html
import json
from pathlib import Path
import re
import sqlite3
import sys

from bs4 import BeautifulSoup, NavigableString, Tag

AUDIT_VERSION="independent-evidence-audit-2.0-unit-qnames"


def independent_soup(content):
    """Keep source positions for exact xmlns spelling; html.parser lowercases attrs."""
    soup=BeautifulSoup(content,"html.parser")
    markup=content if isinstance(content,str) else content.decode(soup.original_encoding or "utf-8",errors="replace")
    soup.__dict__["_audit_source_markup"]=markup
    soup.__dict__["_audit_line_offsets"]=[0]+[match.end() for match in re.finditer("\n",markup)]
    soup.__dict__["_audit_namespace_cache"]={}
    return soup


def source_namespaces(node):
    """Read declarations at this element only, independently from original bytes."""
    root=node
    while root.parent is not None:root=root.parent
    if getattr(root,"is_xml",False):
        return {("" if str(key)=="xmlns" else str(key)[6:]):value
                for key,value in node.attrs.items() if str(key)=="xmlns" or str(key).startswith("xmlns:")}
    if "_audit_source_markup" not in root.__dict__:
        raise ValueError("Exact namespace scope unavailable; parse raw bytes with independent_soup")
    cache=root.__dict__["_audit_namespace_cache"]
    if id(node) in cache:return cache[id(node)]
    if node.sourceline is None or node.sourcepos is None:raise ValueError("Source position unavailable for namespace declaration")
    markup=root.__dict__["_audit_source_markup"]
    offset=root.__dict__["_audit_line_offsets"][node.sourceline-1]+node.sourcepos
    opening=re.compile(r'''<([^\s/>]+)(?:[^'">]|'[^']*'|"[^"]*")*>''').match(markup,offset)
    if not opening or opening[1].lower()!=node.name.lower():raise ValueError("Original opening tag does not match namespace scope")
    fragment=opening[0];position=1+len(opening[1]);declarations={}
    attr_pattern=re.compile(r'''\s+([^\s=/<>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?''')
    while fragment[position:].strip() not in {">","/>"}:
        match=attr_pattern.match(fragment,position)
        if not match:raise ValueError("Ambiguous original opening tag attributes")
        name=match[1]
        if name=="xmlns" or name.startswith("xmlns:"):
            prefix="" if name=="xmlns" else name[6:]
            if prefix in declarations or (match[2] is None and match[3] is None):raise ValueError("Ambiguous/unquoted namespace declaration")
            declarations[prefix]=html.unescape(match[2] if match[2] is not None else match[3])
        position=match.end()
    cache[id(node)]=declarations
    return declarations


def text(node):
    return "".join(str(part) for part in node.descendants if isinstance(part,NavigableString)
                   and not any(local(parent.name)=="exclude" for parent in part.parents if parent is not node))


def local(name):
    return str(name or "").rsplit(":",1)[-1].rsplit("}",1)[-1].lower()


def attribute(node,name,default=None):
    for key,value in node.attrs.items():
        if local(key)==name.lower():return value
    return default


def namespace(node,prefix):
    if prefix=="xml":return "http://www.w3.org/XML/1998/namespace"
    for parent in [node,*node.parents]:
        if isinstance(parent,Tag) and parent.name!="[document]":
            declarations=source_namespaces(parent)
            if prefix in declarations:return declarations[prefix] or None
    return None


def decimal(value):
    if value is None:return None
    if isinstance(value,bool):raise ValueError("Boolean is not a financial number")
    result=Decimal(str(value))
    if not result.is_finite():raise ValueError("Non-finite number")
    return result


def independent_number(node):
    """Small independently implemented whitelist; unsupported inputs stay unknown."""
    original=text(node)
    numeric=original.strip().replace("\xa0"," ")
    transform=attribute(node,"format","")
    transform_name=transform.rsplit(":",1)[-1].lower()
    if attribute(node,"nil","").lower() in {"true","1"}:
        if numeric:raise ValueError("Nil fact has display text")
        return None,original
    if transform:
        prefix=transform.split(":",1)[0] if ":" in transform else ""
        uri=namespace(node,prefix)
        if not uri or not re.fullmatch(r"https?://www\.xbrl\.org/inlineXBRL/transformation/\d{4}-\d{2}-\d{2}",uri):
            raise ValueError("Transform namespace not independently recognized")
    if transform_name in {"num-dot-decimal","numdotdecimal"}:
        # Validate groups before deleting grouping separators.
        parts=numeric.split(".")
        if len(parts)>2 or len(parts)==2 and not parts[1].isdigit():raise ValueError("Invalid decimal fraction")
        whole=parts[0]
        separators=[sep for sep in (","," ") if sep in whole]
        if len(separators)>1:raise ValueError("Mixed grouping separators")
        groups=whole.split(separators[0]) if separators else [whole]
        if not all(g.isdigit() for g in groups):raise ValueError("Invalid integer group")
        if len(groups)>1 and (not 1<=len(groups[0])<=3 or any(len(g)!=3 for g in groups[1:])):
            raise ValueError("Invalid thousand groups")
        numeric="".join(groups)+("."+parts[1] if len(parts)==2 else "")
    elif transform_name in {"num-comma-decimal","numcommadecimal"}:
        parts=numeric.split(",")
        if len(parts)>2 or len(parts)==2 and not parts[1].isdigit():raise ValueError("Invalid comma fraction")
        whole=parts[0]
        separators=[sep for sep in ("."," ") if sep in whole]
        if len(separators)>1:raise ValueError("Mixed grouping separators")
        groups=whole.split(separators[0]) if separators else [whole]
        if not all(g.isdigit() for g in groups):raise ValueError("Invalid integer group")
        if len(groups)>1 and (not 1<=len(groups[0])<=3 or any(len(g)!=3 for g in groups[1:])):
            raise ValueError("Invalid thousand groups")
        numeric="".join(groups)+("."+parts[1] if len(parts)==2 else "")
    elif transform_name in {"num-dash","numdash","zerodash"}:
        if numeric not in {"-","–","—"}:raise ValueError("Dash transform lacks dash")
        numeric="0"
    elif transform_name:
        raise ValueError("Transform outside independent whitelist: "+transform_name)
    elif not re.fullmatch(r"[+-]?(\d+(\.\d*)?|\.\d+)",numeric):
        raise ValueError("Unformatted text is not a canonical decimal")
    exponent=int(attribute(node,"scale","0"))
    if abs(exponent)>99:raise ValueError("Unreasonable scale")
    sign=attribute(node,"sign")
    if sign not in (None,"-") or (sign and numeric.startswith(("-","+"))):raise ValueError("Unsupported/double sign")
    result=decimal(numeric).scaleb(exponent)
    return -result if sign=="-" else result,original


def independent_context(node):
    periods={}
    dimensions=[]
    entity=None
    for child in node.find_all(True):
        name=local(child.name)
        if name in {"startdate","enddate","instant"}:periods[name]=child.get_text(strip=True)
        if name=="identifier":entity={"identifier":child.get_text(strip=True),"scheme":attribute(child,"scheme")}
        if name in {"explicitmember","typedmember"}:
            dimensions.append({"axis":attribute(child,"dimension"),"member":" ".join(child.get_text(" ",strip=True).split()),"type":"explicit" if name=="explicitmember" else "typed"})
    if "instant" not in periods and not {"startdate","enddate"}.issubset(periods):raise ValueError("Unsupported/missing context period")
    return {"period_start":periods.get("startdate"),"period_end":periods.get("instant",periods.get("enddate")),
            "dimensions":sorted(dimensions,key=lambda row:(row["axis"] or "",row["member"])),"entity":entity}


def independent_unit(node):
    # Recognized display prefixes depend on URI, never on a lexical prefix.
    known={"http://www.xbrl.org/2003/iso4217":"iso4217", "http://www.xbrl.org/2003/instance":"xbrli"}
    children=node.find_all(True,recursive=False)
    numerator_nodes,denominator_nodes=[],[]
    if children and all(local(child.name)=="measure" for child in children):
        numerator_nodes=children
    elif len(children)==1 and local(children[0].name)=="divide":
        parts=children[0].find_all(True,recursive=False)
        if len(parts)!=2 or [local(part.name) for part in parts]!=["unitnumerator","unitdenominator"]:
            raise ValueError("Invalid divided unit structure")
        numerator_nodes=parts[0].find_all(True,recursive=False);denominator_nodes=parts[1].find_all(True,recursive=False)
        if not numerator_nodes or not denominator_nodes or any(local(part.name)!="measure" for part in [*numerator_nodes,*denominator_nodes]):
            raise ValueError("Invalid unit measures")
    else:raise ValueError("Unsupported unit structure")
    def expand(measure):
        if measure.find(True):raise ValueError("Nested markup in unit measure")
        lexical=measure.get_text(strip=True)
        # Deliberately bounded independent XML QName lexical validation.
        name=r"[A-Za-z_][A-Za-z0-9_.-]*"
        if not re.fullmatch(name+r"(?::"+name+r")?",lexical):raise ValueError("Unsupported/invalid measure QName")
        prefix,term=lexical.split(":",1) if ":" in lexical else ("",lexical)
        uri=namespace(measure,prefix)
        if prefix and not uri:raise ValueError("Unbound unit namespace prefix: "+prefix)
        return {"namespace_uri":uri,"local_name":term}
    sort=lambda items:sorted((expand(item) for item in items),key=lambda item:(item["namespace_uri"] or "",item["local_name"]))
    numerator,denominator=sort(numerator_nodes),sort(denominator_nodes)
    def label(measure):
        uri,term=measure["namespace_uri"],measure["local_name"]
        return known[uri]+":"+term if uri in known else "{"+uri+"}"+term if uri else term
    top,bottom=[label(item) for item in numerator],[label(item) for item in denominator]
    money="http://www.xbrl.org/2003/iso4217"
    currency=numerator[0]["local_name"] if len(numerator)==1 and numerator[0]["namespace_uri"]==money and not any(item["namespace_uri"]==money for item in denominator) else None
    return {"label":"*".join(top)+("/"+"*".join(bottom) if bottom else ""),"numerator":top,"denominator":bottom,
            "expanded_numerator":numerator,"expanded_denominator":denominator,"currency":currency}


def path_node(soup,locator):
    current=soup
    if not locator or not locator.startswith("/"):return None
    for part in locator.strip("/").split("/"):
        match=re.fullmatch(r"([^\[\]]+)(?:\[(\d+)\])?",part)
        if not match:return None
        children=current.find_all(match.group(1).lower(),recursive=False)
        index=int(match.group(2) or "1")-1
        if index>=len(children):return None
        current=children[index]
    return current


def independent_path(node):
    parts=[]
    for item in [node,*node.parents]:
        if not isinstance(item,Tag) or item.name=="[document]":continue
        peers=item.parent.find_all(item.name,recursive=False) if item.parent else [item]
        index=next(index for index,peer in enumerate(peers,1) if peer is item)
        parts.append(item.name+("["+str(index)+"]" if len(peers)>1 else ""))
    return "/"+"/".join(reversed(parts))


def strata(row):
    data=row["data"];result=set()
    unit=row.get("unit") or ""
    if "/" in unit and "shares" in unit.lower():result.add("per_share")
    elif "shares" in unit.lower():result.add("shares")
    elif row.get("currency"):result.add("money")
    try:
        if decimal(data.get("value")) is not None and decimal(data["value"])<0:result.add("negative")
    except (ValueError,InvalidOperation):pass
    if data.get("scale") not in (None,0,1):result.add("display_scale")
    if data.get("dimensions"):result.add("dimensions")
    if data.get("is_custom_concept"):result.add("custom_concept")
    if data.get("value") is None:result.add("source_nil")
    return result or {"other"}


def select_sample(rows,count):
    # First cover available edge cases, then diversify documents/concepts/periods.
    ordered=sorted(rows,key=lambda row:hashlib.sha256(row["record_id"].encode()).hexdigest())
    selected=[];used=set();covered=set()
    for category in ("negative","display_scale","shares","per_share","custom_concept","dimensions","money","source_nil"):
        candidate=next((row for row in ordered if row["record_id"] not in used and category in strata(row)),None)
        if candidate:
            selected.append(candidate);used.add(candidate["record_id"]);covered.update(strata(candidate))
    while len(selected)<min(count,len(rows)):
        seen_docs={row["raw_id"] for row in selected}
        seen_concepts={row["data"].get("concept_qname",row["data"].get("concept")) for row in selected}
        choices=[row for row in ordered if row["record_id"] not in used]
        candidate=max(choices,key=lambda row:(row["raw_id"] not in seen_docs,row["data"].get("concept_qname",row["data"].get("concept")) not in seen_concepts,len(strata(row)-covered)))
        selected.append(candidate);used.add(candidate["record_id"]);covered.update(strata(candidate))
    return selected


class Audit:
    def __init__(self,root):
        self.root=Path(root).resolve()
        self.db=sqlite3.connect((self.root/"data.sqlite3").as_uri()+"?mode=ro",uri=True)
        self.db.row_factory=sqlite3.Row
        self.db.execute("BEGIN")  # One consistent audit snapshot during a running collector.
        self.blobs={row["raw_id"]:dict(row) for row in self.db.execute("SELECT * FROM raw_blobs")}
        self.hash_results={}

    def verify_raw(self):
        rows=[]
        for raw_id,blob in self.blobs.items():
            path=self.root/blob["path"]
            item={"raw_id":raw_id,"path":str(path),"expected_sha256":blob["sha256"],"expected_byte_size":blob["byte_size"]}
            try:
                digest=hashlib.sha256();size=0
                with path.open("rb") as handle:
                    while chunk:=handle.read(1024*1024):digest.update(chunk);size+=len(chunk)
                item.update(actual_sha256=digest.hexdigest(),actual_byte_size=size,
                            outcome="pass" if digest.hexdigest()==blob["sha256"] and size==blob["byte_size"] else "fail")
            except OSError as exc:item.update(outcome="fail",error=str(exc))
            rows.append(item);self.hash_results[raw_id]=item
        return {"checked":len(rows),"outcomes":dict(Counter(row["outcome"] for row in rows)),"results":rows}

    @lru_cache(maxsize=6)
    def content(self,raw_id):
        if self.hash_results.get(raw_id,{}).get("outcome")!="pass":raise ValueError("Raw source failed independent integrity check")
        return (self.root/self.blobs[raw_id]["path"]).read_bytes()

    @lru_cache(maxsize=3)
    def soup(self,raw_id):
        return independent_soup(self.content(raw_id))

    @lru_cache(maxsize=3)
    def payload(self,raw_id):
        return json.loads(self.content(raw_id),parse_float=Decimal)

    def source_url(self,row):
        retrieval=self.db.execute("SELECT url FROM raw_retrievals WHERE retrieval_id=?",(row["retrieval_id"],)).fetchone()
        return retrieval[0] if retrieval else None

    def base(self,row,method):
        return {"record_id":row["record_id"],"company_id":row["company_id"],"dataset":row["dataset"],"method":method,
                "raw_id":row["raw_id"],"sha256":self.blobs.get(row["raw_id"],{}).get("sha256"),
                "source_url":self.source_url(row),"source_locator":row["source_locator"],"parser_version":row["parser_version"],
                "strata":sorted(strata(row)),"published_at":row["published_at"],"available_at":row["available_at"],"retrieved_at":row["retrieved_at"],
                "expected":None,"actual":{"value":row["data"].get("value"),"unit":row["unit"],"period_start":row["period_start"],"period_end":row["period_end"]},
                "evidence_excerpt":None,"recorded_evidence_excerpt":row["data"].get("evidence_excerpt")}

    def api(self,row):
        result=self.base(row,"independent_saved_SEC_companyfacts_JSON")
        data=row["data"]
        try:
            payload=self.payload(row["raw_id"])
            definition=payload["facts"][data["taxonomy"]][data["concept"]]
            observations=definition["units"][row["unit"]]
            matches=[(index,fact) for index,fact in enumerate(observations)
                     if fact.get("accn")==data.get("accession") and fact.get("start")==row["period_start"]
                     and fact.get("end")==row["period_end"] and all(fact.get(key)==data.get(key) for key in ("fy","fp","frame","form"))]
            if not matches:raise ValueError("No original JSON fact matches accession, periods and context metadata")
            values={str(decimal(fact.get("val"))) for _,fact in matches}
            if len(values)>1:raise ValueError("Original JSON has conflicting values for this fact context")
            index,fact=matches[0]
            expected={"value":str(decimal(fact.get("val"))),"period_start":fact.get("start"),"period_end":fact.get("end"),"unit":row["unit"],
                      "accession":fact.get("accn"),"filed":fact.get("filed"),"company_cik":str(payload.get("cik")).zfill(10)}
            actual={"value":str(decimal(data.get("value"))),"period_start":row["period_start"],"period_end":row["period_end"],"unit":data.get("unit"),
                    "accession":data.get("accession"),"filed":data.get("filed"),"company_cik":str(row["company_id"]).removeprefix("sec:").zfill(10)}
            pointer="/facts/"+data["taxonomy"]+"/"+data["concept"]+"/units/"+row["unit"].replace("~","~0").replace("/","~1")+"/"+str(index)
            result.update(expected=expected,actual=actual,verified_source_locator=pointer,evidence_excerpt=json.dumps(fact,ensure_ascii=False,default=str),
                          outcome="pass" if expected==actual else "fail",checks=["source numeric value","unit key","period bounds","accession","filing date","issuer CIK"])
        except (KeyError,ValueError,TypeError,InvalidOperation) as exc:result.update(outcome="unverified",reason=str(exc))
        return result

    def support_ids(self,row):
        ids=[]
        candidates=[]
        for key in ("support_documents","supporting_raw_ids"):
            values=row["data"].get(key) or []
            candidates.extend(values if isinstance(values,list) else [values])
        for value in candidates:
            identifier=value if isinstance(value,str) else value.get("raw_id") if isinstance(value,dict) else None
            if identifier in self.blobs and identifier!=row["raw_id"]:ids.append(identifier)
        return list(dict.fromkeys(ids))

    def definition(self,row,kind,identifier):
        for raw_id in [row["raw_id"],*self.support_ids(row)]:
            candidates=self.soup(raw_id).find_all(lambda tag:isinstance(tag,Tag) and local(tag.name)==kind and attribute(tag,"id")==identifier)
            if len(candidates)>1:raise ValueError("Duplicated "+kind+" identifier in original source")
            if candidates:return candidates[0],raw_id
        raise ValueError("Original "+kind+" not present in saved document/support documents")

    def inline(self,row):
        result=self.base(row,"independent_BeautifulSoup_html_parser_and_Decimal")
        data=row["data"]
        try:
            soup=self.soup(row["raw_id"])
            def matches(tag):
                return isinstance(tag,Tag) and local(tag.name)=="nonfraction" and attribute(tag,"name")==data["concept_qname"] and attribute(tag,"contextref")==data["context_id"] and attribute(tag,"unitref")==data["unit_id"] and text(tag)==data.get("original_value_text")
            node=path_node(soup,row["source_locator"])
            locator_verified=bool(node and matches(node))
            if not locator_verified:
                candidates=soup.find_all(matches)
                if not candidates:raise ValueError("No original numeric tag matches concept, context, unit and original text")
                if len(candidates)>1:
                    values={str(independent_number(candidate)[0]) for candidate in candidates}
                    if len(values)>1:raise ValueError("Multiple matching original tags disagree")
                node=candidates[0]
            result.update(evidence_excerpt=text(node),source_tag_excerpt=str(node)[:2000],locator_exactly_verified=locator_verified,
                          verified_source_locator=independent_path(node))
            expected_value,excerpt=independent_number(node)
            context_node,context_raw=self.definition(row,"context",attribute(node,"contextref"))
            unit_node,unit_raw=self.definition(row,"unit",attribute(node,"unitref"))
            context=independent_context(context_node);unit=independent_unit(unit_node)
            dimensions=[{key:item.get(key) for key in ("axis","member","type")} for item in data.get("dimensions",[])]
            dimensions.sort(key=lambda item:(item["axis"] or "",item["member"]))
            expected={"value":None if expected_value is None else str(expected_value),"period_start":context["period_start"],"period_end":context["period_end"],
                      "unit":unit["label"],"currency":unit["currency"],"dimensions":context["dimensions"],"entity":context["entity"],
                      "scale":int(attribute(node,"scale","0")),"sign":attribute(node,"sign"),"original_text":excerpt}
            actual={"value":None if data.get("value") is None else str(decimal(data["value"])),"period_start":row["period_start"],"period_end":row["period_end"],
                    "unit":row["unit"],"currency":row["currency"],"dimensions":dimensions,"entity":data.get("entity"),
                    "scale":data.get("scale"),"sign":data.get("sign"),"original_text":data.get("original_value_text")}
            # Decimal equality is numeric; representation differences are not errors.
            numeric_equal=expected_value==decimal(data.get("value"))
            nonnumeric_equal=all(expected[key]==actual[key] for key in expected if key!="value")
            result.update(outcome="pass" if numeric_equal and nonnumeric_equal else "fail",expected=expected,actual=actual,
                          evidence_excerpt=excerpt,source_tag_excerpt=str(node)[:2000],locator_exactly_verified=locator_verified,
                          match_basis="stored path plus independent attributes" if locator_verified else "independent concept/context/unit/text matching; stored XPath not uniquely established",
                          context_evidence={"raw_id":context_raw,"sha256":self.blobs[context_raw]["sha256"],"id":data["context_id"],"excerpt":str(context_node)[:5000]},
                          unit_evidence={"raw_id":unit_raw,"sha256":self.blobs[unit_raw]["sha256"],"id":data["unit_id"],"excerpt":str(unit_node)[:2000],"independent_definition":unit},
                          checks=["Decimal value with original scale/sign/whitelisted transformation","context periods","unit measures resolved by source-scoped namespace URI and local name","currency","dimension labels","entity identifier"])
        except (KeyError,ValueError,TypeError,InvalidOperation) as exc:result.update(outcome="unverified",reason=str(exc))
        return result

    def temporal(self,rows):
        # Reuse only the public query layer with a read-only DB, never a parser.
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
        from equity_data.store import Store
        view=Store.__new__(Store);view.root=self.root;view.db=self.db
        selected=[];seen=set()
        for row in rows:
            identity=(row["company_id"],row["available_at"],row["availability_basis"])
            if identity not in seen:selected.append(row);seen.add(identity)
            if len(selected)>=24:break
        checks=[]
        for row in selected:
            item={"record_id":row["record_id"],"company_id":row["company_id"],"available_at":row["available_at"],"retrieved_at":row["retrieved_at"],"basis":row["availability_basis"]}
            try:
                acquired=datetime.fromisoformat(row["retrieved_at"].replace("Z","+00:00"))
                known_cutoff=(acquired-timedelta(microseconds=1)).isoformat()
                known_ids={r["record_id"] for r in view.query(row["dataset"],company_id=row["company_id"],known_at=known_cutoff,latest=False)}
                item["excluded_before_system_acquisition"]=row["record_id"] not in known_ids
                public_cutoff=(datetime.fromisoformat(row["available_at"].replace("Z","+00:00"))-timedelta(microseconds=1)).isoformat() if row["available_at"] else "2100-01-01"
                public_ids={r["record_id"] for r in view.query(row["dataset"],company_id=row["company_id"],as_of=public_cutoff,latest=False)}
                item["excluded_before_recorded_public_bound"]=row["record_id"] not in public_ids
                item.update(outcome="pass" if item["excluded_before_system_acquisition"] and item["excluded_before_recorded_public_bound"] else "fail",
                            as_of_test_cutoff=public_cutoff,known_at_test_cutoff=known_cutoff)
            except Exception as exc:item.update(outcome="unverified",reason=type(exc).__name__+": "+str(exc))
            checks.append(item)
        clocks=self.db.execute("""SELECT COUNT(*) FROM records r LEFT JOIN raw_retrievals a ON r.retrieval_id=a.retrieval_id
            WHERE a.retrieval_id IS NULL OR r.raw_id!=a.raw_id OR r.retrieved_at!=a.retrieved_at""").fetchone()[0]
        return {"checks":checks,"outcomes":dict(Counter(item["outcome"] for item in checks)),"record_retrieval_link_or_clock_mismatch_count":clocks,
                "limitation":"Boundary checks exercise Store.query; they do not independently prove SEC acceptance equals dissemination or that a current webpage existed historically. Conservative bounds remain assumptions, not verified intraday publication timestamps."}

    def idempotency(self):
        duplicates=self.db.execute("""SELECT COUNT(*) FROM (SELECT semantic_hash,LAG(semantic_hash) OVER
          (PARTITION BY dataset,source,record_key ORDER BY sequence) AS prior_hash FROM records) WHERE semantic_hash=prior_hash""").fetchone()[0]
        names={row[0] for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        runs=[]
        if "machine_runs" in names:
            for row in self.db.execute("SELECT run_id,mode,state,started_at,summary_json FROM machine_runs ORDER BY started_at"):
                summary=json.loads(row["summary_json"] or "{}")
                runs.append({"run_id":row["run_id"],"mode":row["mode"],"state":row["state"],"started_at":row["started_at"],"executed":summary.get("executed"),"succeeded":summary.get("succeeded"),"failed":summary.get("failed"),"remaining_tasks":summary.get("remaining_tasks")})
        return {"adjacent_identical_semantic_record_versions":duplicates,"run_evidence":runs,
                "runtime_replay_executed_by_this_audit":False,
                "limitation":"Read-only consistency and recorded run evidence; no backfill/update was executed by this audit. A->B->A is a valid version sequence, not a duplicate."}

    def run(self,sample_count):
        integrity=self.verify_raw()
        records=[]
        for raw in self.db.execute("""SELECT r.* FROM records r WHERE dataset='financial_facts'
          AND sequence=(SELECT MAX(sequence) FROM records p WHERE p.dataset=r.dataset AND p.source=r.source AND p.record_key=r.record_key)"""):
            row=dict(raw);row["data"]=json.loads(row.pop("data_json"));records.append(row)
        groups=defaultdict(lambda:{"api":[],"inline":[]})
        for row in records:
            data=row["data"]
            if data.get("taxonomy") and data.get("concept"):groups[row["company_id"]]["api"].append(row)
            elif data.get("concept_qname"):groups[row["company_id"]]["inline"].append(row)
        results=[];sampling=[];sampled=[]
        for issuer,kinds in sorted(groups.items(),key=lambda item:str(item[0])):
            for method,candidates in kinds.items():
                selected=select_sample(candidates,sample_count)
                sampled.extend(selected)
                sampling.append({"company_id":issuer,"method":method,"candidate_records":len(candidates),"sampled":len(selected),"minimum_requested":sample_count,
                    "sampling_target_met":len(selected)>=sample_count,"unexamined_candidates":len(candidates)-len(selected),
                    "available_strata":sorted(set().union(*(strata(row) for row in candidates))) if candidates else [],
                    "sampled_strata":sorted(set().union(*(strata(row) for row in selected))) if selected else [],
                    "shortfall_reason":"Fewer saved parsed candidates than requested; no facts fabricated" if len(selected)<sample_count else None})
                results.extend((self.api if method=="api" else self.inline)(row) for row in selected)
        guidance=[dict(row) for row in self.db.execute("SELECT record_id,company_id,raw_id,source_locator,quality FROM records WHERE dataset='guidance'")]
        return {"audit_version":AUDIT_VERSION,"generated_at":datetime.now(timezone.utc).isoformat(),"storage":str(self.root),
                "network_requests":0,"database_open_mode":"read_only","sampling_method":"Deterministic record-ID order, edge-case strata first, then document/concept diversity; latest stored version per semantic key.",
                "sampling":sampling,"field_results":results,"field_outcomes":dict(Counter(item["outcome"] for item in results)),
                "raw_integrity":integrity,"temporal":self.temporal(sampled),"idempotency":self.idempotency(),
                "guidance":{"status":"human_review_pending","record_versions":len(guidance),"records":guidance,
                            "reason":"Free-text guidance meaning, conditions, target periods and tolerances require a separately specified independent audit; no numeric accuracy guarantee made here."},
                "limitations":["Sample checks are not full-dataset verification or a completeness claim.","Unsupported formats/context evidence remain unverified; reported separately from pass/fail.",
                               "Typed dimensions compare axis/member text; full XML/taxonomy semantics are not independently validated.","Raw SHA-256 integrity proves stored-byte consistency, not source truth or licensing rights.",
                               "The same saved official source is reread independently; no independent external vendor reconciliation is performed."]}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage",required=True,type=Path)
    parser.add_argument("--out",required=True,type=Path)
    parser.add_argument("--per-kind",type=int,default=10,help="Minimum sample goal per issuer for API and inline facts (default 10)")
    args=parser.parse_args()
    if args.per_kind<10:parser.error("--per-kind must be at least 10")
    audit=Audit(args.storage)
    try:report=audit.run(args.per_kind)
    finally:audit.db.close()
    json_path=args.out.resolve() if args.out.suffix.lower()==".json" else args.out.resolve()/"independent-field-audit.json"
    json_path.parent.mkdir(parents=True,exist_ok=True)
    json_path.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
    lines=["# Independent saved-evidence audit","",f"Storage: {report['storage']}",f"Audit version: {AUDIT_VERSION}","",
           "Zero network requests. Database opened read-only. Only sampled facts were independently checked.","",
           "## Field sample results","",json.dumps(report["field_outcomes"],ensure_ascii=False),"",
           "| Issuer | Method | Saved candidates | Sampled | Remaining unchecked |","| --- | --- | --- | --- | --- |"]
    for row in report["sampling"]:lines.append(f"| {row['company_id']} | {row['method']} | {row['candidate_records']} | {row['sampled']} | {row['unexamined_candidates']} |")
    lines.extend(["","## Integrity and time","",f"All saved raw blobs checked: {report['raw_integrity']['checked']}; outcomes: {report['raw_integrity']['outcomes']}.",
                  f"Sampled public/query acquisition-boundary checks: {report['temporal']['outcomes']}.","",report["temporal"]["limitation"],"",
                  "## Guidance and remaining validation","",report["guidance"]["reason"],"",*report["limitations"],"",
                  "The complete JSON contains each record ID, source hash/URL/location/excerpt, independent expected values, stored actual values, and outcome."])
    markdown_path=json_path.with_suffix(".md");markdown_path.write_text("\n".join(lines)+"\n",encoding="utf-8")
    summary={"json":str(json_path),"markdown":str(markdown_path),"field_outcomes":report["field_outcomes"],"raw_outcomes":report["raw_integrity"]["outcomes"],"temporal_outcomes":report["temporal"]["outcomes"]}
    print(json.dumps(summary,ensure_ascii=False))
    return 2 if report["field_outcomes"].get("fail") or report["raw_integrity"]["outcomes"].get("fail") or report["temporal"]["outcomes"].get("fail") else 0


if __name__=="__main__":raise SystemExit(main())
