from equity_data.store import Store

def test_bad_extraction_remains_auditable_but_not_a_historical_fact(tmp_path):
    with Store(tmp_path) as store:
        raw=store.put_raw(b'table in millions',source='source',url='https://test/table',media_type='text/html',retrieved_at='2024-01-04T00:00:00Z')
        record=store.put_record('guidance','test',{'value':'123'},source='source',raw_id=raw['raw_id'],available_at='2024-01-02T00:00:00Z',availability_basis='dated_archive')
        store.validate_record(record,verdict='rejected',raw_id=raw['raw_id'],reason='Missing table scale',validated_at='2024-01-05T00:00:00Z')
        assert store.query('guidance')==[]
        assert store.query('guidance',as_of='2024-01-03')==[]
        assert store.query('guidance',as_of='2024-01-03',known_at='2024-01-04')[0]['record_id']==record
        assert store.query('guidance',include_rejected=True)[0]['validation']['verdict']=='rejected'

def test_parser_version_only_is_not_a_source_value_revision(tmp_path):
    with Store(tmp_path) as store:
        raw=store.put_raw(b'same bytes',source='source',url='https://test',media_type='text/plain')
        for version in ['1','2']:
            store.put_record('facts','test',{'value':'8','parser_version':version},source='source',raw_id=raw['raw_id'],
                             available_at='2024-01-02T00:00:00Z',availability_basis='dated_archive',parser_version=version)
        assert len(store.query('facts',as_of='2024-01-02'))==1
        assert store.query('facts',as_of='2024-01-02')[0]['parser_version']=='2'
