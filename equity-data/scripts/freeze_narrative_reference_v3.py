"""Manual model audit annotations. Never imported by production collectors."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json

BASE=Path(__file__).resolve().parents[1]
OUT=BASE/'reports/v3'
catalog=json.loads((OUT/'narrative-raw-catalog.json').read_text(encoding='utf-8'))
docs={item['url'].split('/')[-1]:item for item in catalog['documents']}
targets=[]

def add(name,docname,index,family,expected,unknowns=None):
    doc=docs[docname]; item=doc['candidates'][index]
    targets.append({'target_id':name,'family':family,'source_url':doc['url'],'raw_id':doc['raw_id'],
        'sha256':doc['sha256'],'raw_path':doc['path'],'independent_path':item['independent_path'],
        'evidence_excerpt':item['excerpt'],'expected':expected,'unknown_reasons':unknowns or {},
        'reference_confidence':'manual_model_review_of_explicit_source_span',
        'required_stage':'extracted_fact_with_contract_semantics_not_candidate_or_one_unassembled_number'})

add('guidance_hon_capex_2024','hon-20231231.htm',19,'guidance',
    {'original_metric':'Capital expenditures','point':'1100000000','value_qualifier':'approximately','unit':'dollar_symbol','currency':None,'period_label':'2024','scope':'Honeywell capital expenditures'},
    {'currency':'Only dollar sign in selected span; USD not independently established in this target','period_boundaries':'Year explicit, fiscal/calendar boundaries not independently established in selected span'})
add('guidance_sbux_capex_fy2024','sbux-20240331.htm',25,'guidance',
    {'original_metric':'Total capital expenditures','point':'3000000000','value_qualifier':'approximately','unit':'dollar_symbol','currency':None,'period_label':'fiscal 2024','scope':'Starbucks total capital expenditures'},
    {'currency':'Only dollar sign in selected span','period_end':'Fiscal year boundary not in selected span'})
add('operating_sap_total_cloud_backlog','sap-20231231x20f.htm',29,'operating_metrics',
    {'original_metric':'total cloud backlog','value':'44250000000','unit':'EUR','currency':'EUR','period_end':'2023-12-31','scope':'SAP','currency_basis':'actual currency; constant-currency comparative is a distinct value'})
add('operating_sbux_comparable_transactions','sbux-20240331.htm',15,'operating_metrics',
    {'original_metric':'comparable transactions percentage change','value':'-6','unit':'percent','period_label':'second quarter fiscal 2024 versus prior-year quarter','scope':'Starbucks company-operated stores; consolidated'},
    {'precise_dates':'Dates not explicit in chosen paragraph; no calendar interpolation'})
add('relations_hon_aerospace_supplier_contracts','hon-20231231.htm',15,'relations',
    {'relation_type':'raw_material_supply_base_under_contract','subject':'Honeywell Aerospace','counterparty_name':None,'value':'85','unit':'percent','period_end':'2023-12-31'},
    {'counterparty_name':'Suppliers not named in selected source; must remain unknown','denominator_method':'Source gives supply-base percentage without count/spend weighting definition'})
add('relations_hon_customer_dependence','hon-20231231.htm',67,'relations',
    {'relation_type':'customer_concentration_statement','subject':'Honeywell','counterparty_name':None,'assertion':'sales are not materially dependent on a single customer or a small group of customers','numeric_value':None},
    {'period':'Current annual-report statement, exact measurement period not stated in selected span','materiality_threshold':'Source does not define numerical threshold','counterparty_name':'Source does not name customers'})
add('debt_hon_2023_usd_notes_2029','hon-20231231.htm',57,'debt_maturities',
    {'obligor':'Honeywell','instrument':'2023 USD Senior Notes due 2029','principal':'750000000','currency':'USD','unit':'USD','coupon_percent':'4.25','issuance_date':'2023-05-17','maturity_year':2029},
    {'maturity_date':'Exact day and month not stated in selected span'})
add('debt_hon_2023_euro_notes_2027','hon-20231231.htm',58,'debt_maturities',
    {'obligor':'Honeywell','instrument':'2023 Euro Senior Notes due 2027','principal':'650000000','currency':'EUR','unit':'EUR','coupon_percent':'3.50','issuance_date':'2023-05-17','maturity_year':2027},
    {'maturity_date':'Exact day and month not stated in selected span'})
add('commitment_sap_uncommenced_facility_leases','sap-20231231x20f.htm',142,'commitments',
    {'obligor':'SAP','category':'future minimum payments on facility leases not yet commenced','amount':'37000000','unit':'EUR','currency':'EUR','as_of':'2023-12-31'},
    {'payment_dates':'Aggregate amount only; payment timing not in selected span'})
add('commitment_sbux_fixed_coffee','sbux-20240331.htm',6,'commitments',
    {'obligor':'Starbucks','category':'green coffee fixed-price purchase contracts','amount':'398600000','unit':'dollar_symbol','currency':None,'as_of':'2024-03-31','measurement':'fixed_price'},
    {'currency':'Only dollar sign in selected span','delivery_dates':'Specific schedule not disclosed in selected paragraph','counterparty':'Unnamed suppliers'})
add('commitment_sbux_price_to_be_fixed_coffee','sbux-20240331.htm',6,'commitments',
    {'obligor':'Starbucks','category':'green coffee price-to-be-fixed purchase contracts','amount':'855600000','unit':'dollar_symbol','currency':None,'as_of':'2024-03-31','measurement':'company_estimate_not_fixed_obligation_amount'},
    {'currency':'Only dollar sign in selected span','delivery_dates':'Specific schedule not in paragraph','final_amount':'Price not yet fixed; amount explicitly estimated'})

reference={'reference_version':'raw-first-narrative-audit-v1','created_at_before_record_comparison':datetime.now(timezone.utc).isoformat(),
    'author':'model manual source review, same engineering team; not independent human or second vendor',
    'raw_only_before_reference_freeze':True,'production_parser_used_to_build_expected_values':False,
    'selection':'Purposive 11 targets from three available primary filings selected after production freeze and before reading normalized records; at least 2 per requested family',
    'limitations':['Non-random fields; no population accuracy or recall estimate','Three saved primary filings, not all issuers/documents or all target fields','BS4 keyword catalog assists locating source spans; reviewer reads and labels spans manually','Unknown scope/time/currency not inferred beyond each evidence span','Candidate snippet or isolated XBRL value does not satisfy an assembled relation, debt, commitment or operating metric contract'],
    'targets':targets}
path=OUT/'narrative-reference.json'
assert not path.exists(),'Do not overwrite frozen reference'
payload=json.dumps(reference,indent=2,ensure_ascii=False).encode('utf-8')
path.write_bytes(payload)
manifest={'reference_file':str(path.relative_to(BASE)), 'sha256':hashlib.sha256(payload).hexdigest(), 'frozen_at_before_record_comparison':reference['created_at_before_record_comparison'],'targets':len(targets)}
(OUT/'narrative-reference-freeze.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
print(json.dumps(manifest))
