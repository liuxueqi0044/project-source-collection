from unittest.mock import patch
from equity_data.store import Store


def test_reparse_today_not_present_in_old_processed_system_state(tmp_path):
    with Store(tmp_path) as store:
        raw=store.put_raw(b'synthetic original',source='fixture',url='fixture:raw',retrieved_at='2020-01-01T00:00:00Z')
        with patch('equity_data.store.now_utc',return_value='2020-01-02T00:00:00.000000Z'):
            original=store.put_record('financial_facts','metric',{'value':1},source='fixture',raw_id=raw['raw_id'])
        with patch('equity_data.store.now_utc',return_value='2026-01-02T00:00:00.000000Z'):
            corrected=store.put_record('financial_facts','metric',{'value':2},source='fixture',raw_id=raw['raw_id'])
        assert store.query('financial_facts',known_at='2020-12-31')[0]['record_id']==corrected
        assert store.query('financial_facts',known_at='2020-12-31',processed_at='2020-12-31')[0]['record_id']==original
        assert store.query('financial_facts',processed_at='2026-12-31')[0]['record_id']==corrected
        assert store.query('financial_facts',processed_at='2019-12-31')==[]


def test_later_rejection_not_injected_into_earlier_processed_state(tmp_path):
    with Store(tmp_path) as store:
        raw=store.put_raw(b'synthetic original',source='fixture',url='fixture:raw',retrieved_at='2020-01-01T00:00:00Z')
        with patch('equity_data.store.now_utc',return_value='2020-01-02T00:00:00.000000Z'):
            record=store.put_record('guidance','metric',{'value':1},source='fixture',raw_id=raw['raw_id'])
        store.validate_record(record,verdict='rejected',reason='later audit',raw_id=raw['raw_id'],validated_at='2026-01-01T00:00:00Z')
        assert len(store.query('guidance',processed_at='2020-12-31'))==1
        assert store.query('guidance',processed_at='2026-12-31')==[]
