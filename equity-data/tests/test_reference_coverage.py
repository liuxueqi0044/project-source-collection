import copy
import pytest
from equity_data.store import Store
from equity_data.queue import Queue
from equity_data.coverage import evaluate_reference, freshness_matrix


def setup(store):
    raw=store.put_raw(b'<p>Revenue 15 USD</p>',source='test',url='https://example.test/report')
    evidence={'raw_id':raw['raw_id'],'sha256':raw['sha256'],'locator':'/p'}
    base={'schema_version':'coverage-reference-1','reference_provenance':{'method':'human reads raw first','uses_collector_outputs':False},
          'documents':[{'reference_id':'doc','source_id':'test','data_type':'documents','url':'https://example.test/report','evidence':evidence}],
          'fields':[{'reference_id':'fact','source_id':'test','dataset':'financial_facts','evidence':evidence,
                     'selector':{'data.metric':'Revenue'},'expected':{'data.value':'15'},'tolerances':{'data.value':'0'}}]}
    return raw,base


def test_unknown_denominator_does_not_become_one_hundred_percent(tmp_path):
    with Store(tmp_path) as s:
        report=evaluate_reference(s,{'schema_version':'coverage-reference-1','reference_provenance':{'method':'none yet','uses_collector_outputs':False}})
        assert report['fields_summary']['success_rate'] is None
        assert report['whole_source_coverage_complete'] is None


def test_silent_omission_and_undiscovered_reference_retained(tmp_path):
    with Store(tmp_path) as s:
        raw,manifest=setup(s)
        report=evaluate_reference(s,manifest)
        assert report['documents_summary']['outcomes']=={'not_discovered':1}
        assert report['fields_summary']['outcomes']=={'silent_omission':1}


def test_candidate_is_not_fact_and_wrong_value_is_not_success(tmp_path):
    with Store(tmp_path) as s:
        raw,m=setup(s)
        s.put_record('financial_facts','one',{'metric':'Revenue','value':'15','fact_stage':'candidate'},source='test',raw_id=raw['raw_id'])
        assert evaluate_reference(s,m)['fields_summary']['outcomes']=={'candidate_only':1}
        s.put_record('financial_facts','one',{'metric':'Revenue','value':'150','fact_stage':'extracted_fact'},source='test',raw_id=raw['raw_id'])
        assert evaluate_reference(s,m)['fields_summary']['outcomes']=={'extracted_mismatch':1}
        s.put_record('financial_facts','one',{'metric':'Revenue','value':'15','fact_stage':'extracted_fact'},source='test',raw_id=raw['raw_id'])
        assert evaluate_reference(s,m)['fields_summary']['outcomes']=={'extracted_correct':1}


def test_rejection_must_match_exact_original_target(tmp_path):
    with Store(tmp_path) as s:
        raw,m=setup(s);q=Queue(s)
        q.gap('NUMERIC_REJECTED','other field',source_id='test',details={'raw_id':raw['raw_id'],'source_locator':'/p[2]'})
        assert evaluate_reference(s,m)['fields_summary']['outcomes']=={'silent_omission':1}
        q.gap('NUMERIC_REJECTED','target field',source_id='test',details={'raw_id':raw['raw_id'],'source_locator':'/p'})
        assert evaluate_reference(s,m)['fields_summary']['outcomes']=={'explicitly_rejected':1}


def test_reference_independence_and_evidence_integrity_required(tmp_path):
    with Store(tmp_path) as s:
        raw,m=setup(s)
        invalid=copy.deepcopy(m);invalid['reference_provenance']['uses_collector_outputs']=True
        with pytest.raises(ValueError):evaluate_reference(s,invalid)
        (s.root/raw['path']).write_bytes(b'changed evidence')
        with pytest.raises(ValueError):evaluate_reference(s,m)


def test_fresh_source_does_not_hide_older_company(tmp_path):
    rows=[{'source_id':'s','company_id':company,'data_type':'documents','state':'parsed','raw_id':'x','retrieved_at':date,'last_success_at':'2026-10-01T00:00:00Z','metadata':{}}
          for company,date in [('old','2020-01-01T00:00:00Z'),('new','2026-01-01T00:00:00Z')]]
    report=freshness_matrix(rows)
    assert len(report)==2
    assert next(r for r in report if r['company_id']=='old')['newest_resource_acquisition'].startswith('2020')
    assert all(r['disclosure_update_delay_seconds'] is None for r in report)


def test_duplicate_occurrence_cannot_substitute_for_missing_target(tmp_path):
    with Store(tmp_path) as s:
        raw,m=setup(s);m['fields'][0]['selector']['source_locator']='/p[2]'
        s.put_record('financial_facts','one',{'metric':'Revenue','value':'15','fact_stage':'extracted_fact'},source='test',raw_id=raw['raw_id'],source_locator='/p[1]')
        assert evaluate_reference(s,m)['fields_summary']['outcomes']=={'target_locator_unresolved':1}


def test_manual_import_cannot_count_as_automatic_success(tmp_path):
    with Store(tmp_path) as s:
        raw,m=setup(s)
        s.put_record('financial_facts','one',{'metric':'Revenue','value':'15','fact_stage':'extracted_fact','import_manifest_raw_id':'example'},
                     source='test',raw_id=raw['raw_id'],quality='manual_import_unverified',parser_version='manual-jsonl-v1')
        assert evaluate_reference(s,m)['fields_summary']['outcomes']=={'manual_only':1}


def test_offline_reparse_clock_does_not_refresh_raw_acquisition(tmp_path):
    with Store(tmp_path) as s:
        raw=s.put_raw(b'old raw',source='test',url='test:raw',retrieved_at='2020-01-01T00:00:00Z')
        rows=[{'source_id':'s','company_id':'issuer','data_type':'documents','state':'parsed','raw_id':raw['raw_id'],
               'retrieval_id':raw['retrieval_id'],'last_success_at':'2026-10-09T00:00:00Z','metadata':{}}]
        result=freshness_matrix(rows,s)[0]
        assert result['newest_resource_acquisition'].startswith('2020')
        assert result['latest_parse_completion'].startswith('2026')


def document_resource(raw, state='parsed'):
    return {'resource_id':'synthetic-resource','source_id':'test','resource_key':'report','data_type':'documents',
            'company_id':None,'url':'https://example.test/report','state':state,
            'raw_id':raw['raw_id'] if raw else None,'retrieval_id':raw['retrieval_id'] if raw else None,
            'last_discovered_at':'2026-10-09T00:00:00Z','metadata':{}}


def test_saved_original_remains_downloaded_when_new_generation_is_pending(tmp_path):
    with Store(tmp_path) as s:
        raw,manifest=setup(s);manifest['fields']=[]
        resource=document_resource(raw)
        initial=evaluate_reference(s,manifest,resources=[resource],gaps=[])
        resource['state']='discovered_not_downloaded'
        pending=evaluate_reference(s,manifest,resources=[resource],gaps=[])
        assert initial['documents_summary']['success_numerator']==1
        assert pending['documents_summary']['downloaded']==1
        assert pending['documents_summary']['success_numerator']==1
        assert pending['documents_summary']['success_rate']==1
        assert pending['documents_summary']['outcomes']=={'discovered_not_downloaded':1}
        assert pending['by_source_company_type'][0]['documents']['success_numerator']==1
        assert pending['documents_summary']['success_basis']=='downloaded_has_saved_raw'
        assert pending['documents_summary']['saved_raw_with_pending_or_failed_workflow']==1
        assert pending['documents'][0]['latest_requested_version_downloaded'] is None
        assert pending['documents'][0]['workflow_state']=='discovered_not_downloaded'


def test_parsed_workflow_without_saved_original_is_not_download_success(tmp_path):
    with Store(tmp_path) as s:
        _,manifest=setup(s);manifest['fields']=[]
        report=evaluate_reference(s,manifest,resources=[document_resource(None,'parsed')],gaps=[])
        assert report['documents_summary']['downloaded']==0
        assert report['documents_summary']['success_numerator']==0
        assert report['documents_summary']['success_rate']==0
        assert report['documents_summary']['outcomes']=={'parsed':1}
        assert report['by_source_company_type'][0]['documents']['success_numerator']==0


@pytest.mark.parametrize('state',['download_failed','missing_authorization','source_conflict','partial_parse','downloaded_unparsed'])
def test_saved_original_coverage_does_not_depend_on_refresh_or_parse_state(tmp_path,state):
    with Store(tmp_path) as s:
        raw,manifest=setup(s);manifest['fields']=[]
        report=evaluate_reference(s,manifest,resources=[document_resource(raw,state)],gaps=[])
        assert report['documents_summary']['success_numerator']==1
        assert report['documents_summary']['workflow_state_counts']=={state:1}
        assert report['documents_summary']['latest_requested_version_downloaded'] is None
        assert report['by_source_company_type'][0]['documents']['success_rate']==1


@pytest.mark.parametrize('failure',['missing_file','hash_mismatch','missing_blob'])
def test_inventory_pointer_without_intact_file_does_not_count_as_saved_original(tmp_path,failure):
    with Store(tmp_path) as s:
        raw,manifest=setup(s);manifest['fields']=[]
        # Reference evidence is a separately preserved source index. Losing the
        # target document must not invalidate the independent expected-item list.
        listing=s.put_raw(b'<a href="report">source index</a>',source='test',url='https://example.test/index')
        manifest['documents'][0]['evidence']={'raw_id':listing['raw_id'],'sha256':listing['sha256'],'locator':'/a'}
        resource=document_resource(raw,'parsed')
        if failure=='missing_file':
            (s.root/raw['path']).unlink()
        elif failure=='hash_mismatch':
            (s.root/raw['path']).write_bytes(b'corrupt saved original')
        else:
            resource['raw_id']='sha256:not-in-blob-table'
        report=evaluate_reference(s,manifest,resources=[resource],gaps=[])
        assert report['documents_summary']['success_numerator']==0
        assert report['documents_summary']['downloaded']==0
        assert report['documents_summary']['workflow_state_counts']=={'parsed':1}
        assert report['documents'][0]['saved_originals'][0]['saved'] is False
