from equity_data.store import Store

def test_later_time_evidence_excludes_early_assumption_and_retains_system_history(tmp_path):
    with Store(tmp_path) as store:
        raw=store.put_raw(b'filed metadata',source='source',url='https://test/data',media_type='text/plain',retrieved_at='2024-01-04T00:00:00Z')
        record=store.put_record('financial_facts','immutable-accession-fact',{'value':'8'},source='source',raw_id=raw['raw_id'],available_at='2024-01-02T00:00:00Z',availability_basis='date_assumption')
        assert len(store.query('financial_facts',as_of='2024-01-02'))==1
        header=store.put_raw(b'late acceptance',source='source',url='https://test/header',media_type='text/plain',retrieved_at='2024-01-05T00:00:00Z')
        store.restrict_availability('financial_facts','immutable-accession-fact',source='source',not_before='2024-01-03T00:00:00Z',raw_id=header['raw_id'],basis='header_evidence')
        assert store.query('financial_facts',as_of='2024-01-02')==[]
        assert store.query('financial_facts',as_of='2024-01-03')[0]['record_id']==record
        assert len(store.query('financial_facts',as_of='2024-01-02',known_at='2024-01-04'))==1

def test_known_at_requires_cross_document_context_evidence(tmp_path):
    with Store(tmp_path) as store:
        raw=store.put_raw(b'fact',source='source',url='https://test/fact',media_type='text/html',retrieved_at='2024-01-03T00:00:00Z')
        support=store.put_raw(b'unit',source='source',url='https://test/unit',media_type='text/html',retrieved_at='2024-01-05T00:00:00Z')
        record=store.put_record('financial_facts','fact',{'value':'8','supporting_raw_ids':[support['raw_id']]},source='source',raw_id=raw['raw_id'],available_at='2024-01-02T00:00:00Z',availability_basis='dated_archive')
        store.db.execute('INSERT INTO record_evidence VALUES(?,?,?)',(record,support['retrieval_id'],support['raw_id']))
        store.db.commit()
        assert store.query('financial_facts',as_of='2024-01-02',known_at='2024-01-04')==[]
        assert len(store.query('financial_facts',as_of='2024-01-02',known_at='2024-01-05'))==1
