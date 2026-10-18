from equity_data.contracts import CONTRACTS, annotate_contract, validate_contract
from equity_data.parsers import parse_document


def test_contract_families_and_evidence_stages_are_explicit():
    assert {'financial_facts', 'guidance', 'operating_metrics', 'relations', 'debt_maturities', 'commitments'} <= CONTRACTS.keys()
    result = parse_document(b'<p>One customer represented 24% of revenue. Purchase commitments exist with suppliers. Notes mature in 2030.</p>')
    assert result['observations']
    for row in result['observations']:
        assert row['data']['fact_stage'] == 'candidate'
        assert row['data']['semantic_contract']['normalized_metric'] is None
        assert row['data']['semantic_contract']['unknown_reasons']
        assert validate_contract(row) == []


def test_guidance_unknown_currency_fiscal_end_and_entity_are_not_fabricated():
    row = parse_document(b'<p>For fiscal 2031 revenue expected $3 billion.</p>')['observations'][0]
    contract = row['data']['semantic_contract']
    assert contract['stage'] == 'extracted_fact'
    assert {'currency', 'period_end', 'entity_identifier', 'accounting_basis'} <= contract['unknown_reasons'].keys()
    assert row['currency'] is None
    assert contract['applicable_entity']['identifier'] is None
    assert contract['verification_status'] == 'parsed_unverified'
    assert validate_contract(row) == []


def test_candidate_cannot_be_relabelled_fact_without_contract_error():
    row = parse_document(b'<p>Purchase commitments exist with suppliers.</p>')['observations'][0]
    row['data']['semantic_contract']['stage'] = 'extracted_fact'
    assert 'candidate_must_not_be_counted_as_fact' in validate_contract(row)


def test_snapshot_hash_join_reference_is_explicit_and_not_a_payload_revision():
    row = {'dataset': 'financial_facts', 'source_locator': 'facts/us-gaap/Revenue/units/USD/0', 'period_end': '2031-12-31',
           'unit': 'USD', 'currency': 'USD', 'quality': 'parsed_unverified',
           'data': {'concept_qname': 'us-gaap:Revenue', 'value': 10, 'parser_version': 'sec-v3',
                    'entity': {'identifier': '123', 'scheme': 'https://www.sec.gov/CIK'}, 'accounting_basis': 'US-GAAP'}}
    annotate_contract(row, content_hash_reference=True)
    evidence = row['data']['semantic_contract']['evidence']
    assert 'content_sha256' not in evidence
    assert evidence['content_sha256_reference'] == 'records.raw_id -> raw_blobs.sha256'
    assert validate_contract(row) == []
    evidence['content_sha256_reference'] = 'some unspecified raw file'
    assert 'missing_source_evidence' in validate_contract(row)
