"""Regression: prefilled manifest answers cannot pass as automatic parsing."""
import json

from equity_data.public_docs import collect_public_documents, import_materials
from equity_data.store import Store


class Client:
    def __init__(self, store, content):
        self.store, self.content, self.calls = store, content, 0

    def fetch(self, url, *, source, **kwargs):
        self.calls += 1
        return self.content, self.store.put_raw(self.content, source=source, url=url,
            retrieved_at=f'2026-10-09T00:{self.calls:02d}:00Z')


def manifest(tmp_path, records=None):
    item = {'id':'generic-release','url':'https://issuer.test/release','source':'public_test','title':'Test release',
            'company_id':'sec:test','document_type':'earnings_release','published_date':'2025-02-01',
            'publication_timezone':'America/New_York'}
    if records is not None:
        item['records'] = records
        item['required_patterns'] = ['Revenue', 'expected']
    path = tmp_path / 'documents.json'
    path.write_text(json.dumps({'documents':[item]}), encoding='utf-8')
    return {'_base_dir': str(tmp_path), 'public_documents': {'manifest':path.name}}


def test_prefilled_values_rejected_even_when_keywords_match(tmp_path):
    with Store(tmp_path/'store') as store:
        config = manifest(tmp_path, [{'dataset':'guidance','record_key':'answer','data':{'low':999999}}])
        client = Client(store, b'<p>For fiscal 2028 Revenue is expected to be USD 10 million.</p>')
        result = collect_public_documents(store,client,config)
        assert result['status'] == 'partial'
        assert result['raw_download_count'] == 1
        assert result['automatic_extracted_count'] == 0
        assert store.query('guidance') == []
        assert len(store.query('documents')) == 1
        assert store.db.execute("SELECT count(*) FROM quality_issues WHERE code='PREFILLED_MANIFEST_REJECTED'").fetchone()[0] == 1


def test_automatic_reparse_version_and_idempotence(tmp_path):
    with Store(tmp_path/'store') as store:
        config = manifest(tmp_path)
        client = Client(store, b'<p>For fiscal 2028 Revenue is expected to be USD 10 million.</p>')
        first = collect_public_documents(store,client,config)
        second = collect_public_documents(store,client,config)
        assert first['automatic_extracted_count'] == second['automatic_extracted_count'] == 1
        assert len(store.query('guidance', latest=False)) == 1
        client.content = client.content.replace(b'10 million',b'12 billion')
        third = collect_public_documents(store,client,config)
        rows = store.query('guidance',latest=False)
        assert third['automatic_extracted_count'] == 1
        assert len(rows) == 2 and rows[1]['supersedes'] == rows[0]['record_id']
        assert rows[1]['data']['point'] == '12000000000'
        assert rows[1]['raw_id'] != rows[0]['raw_id']
        assert store.query('guidance', as_of='2025-02-02') == []


def test_manual_import_keeps_distinct_status(tmp_path):
    (tmp_path/'evidence.txt').write_text('Human excerpt of authorized material.',encoding='utf-8')
    entry = dict(dataset='guidance',record_key='manual',source='human',source_url='https://issuer.test/report',
        source_locator='page 1', evidence_path='evidence.txt',authorization_note='Public test material',rights='public',
        classification='public_or_authorized_nonconfidential',knowledge_type='company_statement',data={'point':'10'})
    (tmp_path/'manual.jsonl').write_text(json.dumps(entry)+'\n',encoding='utf-8')
    with Store(tmp_path/'store') as store:
        assert import_materials(store,tmp_path/'manual.jsonl')['count'] == 1
        assert store.query('guidance')[0]['quality'] == 'manual_import_unverified'
