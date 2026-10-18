import hashlib

import pytest

from equity_data.queue import Queue
from equity_data.store import Store


@pytest.fixture
def queue(tmp_path):
    with Store(tmp_path) as store:
        yield Queue(store)


def resource(key="doc", **kwargs):
    return {"source_id":"fixture", "resource_key":key, "kind":"document", "data_type":"documents",
            "url":"https://example.invalid/"+key,"metadata":{}, **kwargs}


def test_inventory_and_work_are_idempotent_but_update_keeps_new_demand(queue):
    first = queue.emit(resource(), "backfill")
    assert queue.emit(resource(), "backfill") == first
    assert len(queue.resources()) == len(queue.tasks()) == 1
    queue.emit(resource(), "update:1")
    assert len(queue.resources()) == 1
    assert len(queue.tasks()) == 1
    assert {r[0] for r in queue.db.execute("SELECT generation FROM machine_task_demands")}=={"backfill","update:1"}


def test_discovery_identity_includes_scope(queue):
    first=queue.enqueue_discovery("fixture",{"company_id":"a"})
    assert queue.enqueue_discovery("fixture",{"company_id":"a"})==first
    queue.enqueue_discovery("fixture",{"company_id":"b"})
    assert len(queue.tasks())==2


def test_lease_recovery_does_not_steal_active_owner(queue):
    queue.emit(resource())
    run=queue.start_run("backfill","backfill")
    task=queue.claim(run)
    queue.db.execute("UPDATE machine_tasks SET lease_until='2000-01-01T00:00:00Z'")
    queue.db.commit()
    assert queue.recover_expired()==0
    queue.db.execute("UPDATE machine_runs SET lease_until='2000-01-01T00:00:00Z'")
    queue.db.commit()
    assert queue.recover_expired()==1
    assert queue.tasks()[0]["state"]=="pending"
    assert queue.db.execute("SELECT state FROM machine_attempts").fetchone()[0]=="interrupted"


def test_raw_checkpoint_preserves_exact_retrieval_and_hash(queue):
    queue.emit(resource())
    run=queue.start_run("backfill","backfill")
    task=queue.claim(run)
    raw=queue.store.put_raw(b"original",source="fixture",url="https://example.invalid/doc")
    queue.acquisition_checkpoint(task,raw)
    queue.store.put_raw(b"newer",source="fixture",url="https://example.invalid/doc")
    content,checkpoint=queue.raw_for_task(task)
    assert content==b"original"
    assert checkpoint["retrieval_id"]==raw["retrieval_id"]
    assert queue.resources()[0]["state"]=="downloaded_unparsed"
    assert queue.db.execute("SELECT COUNT(*) FROM machine_resource_versions").fetchone()[0]==1


def test_corrupt_raw_fails_closed(queue):
    queue.emit(resource())
    task=queue.claim(queue.start_run("backfill","backfill"))
    raw=queue.store.put_raw(b"good",source="fixture",url="https://example.invalid/doc")
    queue.acquisition_checkpoint(task,raw)
    from pathlib import Path
    Path(raw["path"]).write_bytes(b"tampered")
    with pytest.raises(ValueError,match="SHA-256"):
        queue.raw_for_task(task)


def test_pending_metadata_enriched_without_duplicate_task(queue):
    queue.emit(resource(metadata={"published_at":None}))
    queue.emit(resource(metadata={"published_at":"2025-01-01T22:00:00Z","immutable_archive":True}))
    assert len(queue.tasks())==1
    assert queue.tasks()[0]["resource"]["metadata"]["immutable_archive"] is True


def test_saved_doc_reprocessed_when_metadata_strengthens(queue):
    queue.emit(resource())
    task=queue.claim(queue.start_run("backfill","backfill"))
    raw=queue.store.put_raw(b"original",source="fixture",url="https://example.invalid/doc")
    queue.acquisition_checkpoint(task,raw)
    queue.finish(task,{"status":"parsed"},resource_state="parsed")
    queue.emit(resource(metadata={"immutable_archive":True}))
    assert queue.tasks()[0]["state"]=="pending"
    assert queue.tasks()[0]["raw_id"]==raw["raw_id"]


def test_gap_retains_history_and_can_be_resolved(queue):
    gap=queue.gap("MISSING","field unknown",source_id="fixture",details={"field":"x"})
    assert queue.gap("MISSING","field unknown",source_id="fixture",details={"field":"x"})==gap
    assert queue.gaps()[0]["occurrences"]==2
    queue.resolve_gaps(source_id="fixture",code="MISSING")
    assert not queue.gaps()
    assert queue.gaps(unresolved=False)[0]["resolved_at"]


def test_reprocess_discovery_does_not_schedule_missing_download(queue):
    queue.reprocess_only=True
    queue.emit(resource(),"reprocess:1")
    assert not queue.tasks()
    assert queue.gaps()[0]["code"]=="RAW_NOT_SAVED"
