import importlib.util
import json
from pathlib import Path
import sys
import pytest
from equity_data.store import Store


def test_reference_reads_escaped_sec_header_without_machine_outputs(tmp_path):
    scripts=Path(__file__).resolve().parents[1]/'scripts'
    sys.path.insert(0,str(scripts))
    try:
        from build_reference_v3 import build
        with Store(tmp_path/'db') as store:
            store.put_raw(json.dumps({'filings':{'recent':{'accessionNumber':['0000009999-24-000001'],
                'filingDate':['2024-01-02'],'form':['8-K'],'primaryDocument':['report.htm']}}}).encode(),
                source='sec_edgar',url='https://data.sec.gov/submissions/CIK0000009999.json')
            base='https://www.sec.gov/Archives/edgar/data/9999/000000999924000001/'
            store.put_raw(b'&lt;DOCUMENT&gt;\n&lt;TYPE&gt;EX-99.1\n&lt;FILENAME&gt;release.htm\n&lt;/DOCUMENT&gt;\n&lt;DOCUMENT&gt;\n&lt;TYPE&gt;XML\n&lt;FILENAME&gt;support.xml\n&lt;/DOCUMENT&gt;',
                          source='sec_edgar',url=base+'0000009999-24-000001-index-headers.html')
        out=tmp_path/'ref.json'
        result=build(tmp_path/'db','2024-01-01','2024-01-31',{'8-K'},out,support_types=['XML'])
        assert result['documents']==3
        assert result['fields']==0
        manifest=json.loads(out.read_text())
        assert {r['url'].rsplit('/',1)[-1] for r in manifest['documents']}=={'report.htm','release.htm','support.xml'}
        with pytest.raises(ValueError,match='already exists'):
            build(tmp_path/'db','2024-01-01','2024-01-31',{'8-K'},out)
    finally:sys.path.remove(str(scripts))
