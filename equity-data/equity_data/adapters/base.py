from __future__ import annotations
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from ..store import now_utc


class DependencyMissing(RuntimeError):
    pass


class UnsupportedCapability(RuntimeError):
    pass


@dataclass
class Context:
    store: object
    client: object
    config: dict
    queue: object
    run_id: str

    def emit(self, resource):
        return self.queue.emit(resource, generation=self.config.get('_generation','backfill'))

    def gap(self,code,message,*,source_id,company_id=None,data_type=None,status='capability_gap',details=None):
        return self.queue.gap(code,message,source_id=source_id,company_id=company_id,
                              data_type=data_type,status=status,details=details,run_id=self.run_id)


class BaseAdapter:
    source_id='base'
    scope_kind='global'
    data_types=()

    def discover(self,ctx,scope):
        raise UnsupportedCapability(self.source_id+': discovery not implemented')

    def backfill(self,ctx,scope):
        return self.discover(ctx,scope)

    def incremental(self,ctx,scope):
        return self.discover(ctx,scope)

    def acquire(self,ctx,resource):
        meta=resource.get('metadata',{})
        return ctx.client.fetch(resource['url'],source=self.source_id,params=meta.get('params'),refresh=meta.get('refresh',True))

    def parse(self,ctx,resource,content,raw):
        raise UnsupportedCapability(self.source_id+': parser not implemented')

    def health(self,ctx):
        return {'status':'implemented','source_id':self.source_id,'capabilities':list(self.data_types),
                'verification':'runtime evidence determines connectivity; not a coverage guarantee'}


def persist_observations(ctx,resource,result,raw):
    """Attach each parsed claim to this exact source version, not a preset answer."""
    ids=[]
    issues=list(result.get('issues',[]))
    rejected=0
    tombstones=0
    produced=set()
    meta=resource.get('metadata',{})
    meta={**meta,**meta.get('evidence',{})}
    evidence_source=raw.get('source',resource['source_id'])
    for obs in result.get('observations',[]):
        data=dict(obs.get('data',{}))
        excerpt=obs.get('evidence_excerpt')
        locator=obs.get('source_locator')
        if not excerpt or not locator:
            rejected+=1
            issues.append({'code':'PARSER_EVIDENCE_MISSING','message':'Observation rejected because excerpt or locator missing'})
            ctx.gap('PARSER_EVIDENCE_MISSING','Parser output rejected: evidence excerpt and locator required.',
                    source_id=resource['source_id'],company_id=resource.get('company_id'),data_type=obs.get('dataset'),status='partial_parse')
            continue
        data.update(evidence_excerpt=excerpt,source_content_sha256=raw['sha256'],resource_key=resource['resource_key'],
                    extraction_mode='automatic',source_url=resource.get('url'),
                    original_published_at=meta.get('published_at'),source_revision_at=meta.get('updated_at'))
        key=resource['resource_key']+'|'+str(obs['record_key'])
        produced.add((obs['dataset'],key))
        # Current web copy and a newly parsed changed source version must never
        # inherit a former public date. Accessions are separately identified archives.
        archived=meta.get('immutable_archive',False)
        available=meta.get('available_at') if archived else raw['retrieved_at']
        basis=meta.get('availability_basis','unknown') if archived else 'first_observed_current_document_version'
        prior=ctx.store.db.execute('SELECT record_id,data_json,available_at,availability_basis FROM records WHERE dataset=? AND source=? AND record_key=? ORDER BY sequence DESC LIMIT 1',
                                   (obs['dataset'],evidence_source,key)).fetchone()
        if prior and json.loads(prior['data_json'])==data and not archived:
            available=prior['available_at']
        if prior:
            previous_data=json.loads(prior['data_json'])
            validation=ctx.store.db.execute('SELECT verdict FROM record_validations WHERE record_id=? ORDER BY validated_at DESC,rowid DESC LIMIT 1',
                                            (prior['record_id'],)).fetchone()
            recovering=(previous_data.get('status')=='not_emitted_by_current_parser' or
                        (validation is not None and validation['verdict']=='rejected'))
            recovery_basis='first_observed_parser_field_recovery'
            if recovering:
                # A recovered extraction is a new processing decision, not proof
                # that a formerly rejected field was usable at its filing date.
                # Keep the old rejected record and tombstone; do not let Store's
                # dated archive replay optimization silently revive either one.
                evidence_times=[value for value in (available,raw['retrieved_at'],prior['available_at'],now_utc()) if value]
                for supporting_raw_id in data.get('supporting_raw_ids',[]):
                    support_time=ctx.store.db.execute('SELECT MAX(retrieved_at) FROM raw_retrievals WHERE raw_id=? AND source=?',
                                                      (supporting_raw_id,evidence_source)).fetchone()[0]
                    if support_time:evidence_times.append(support_time)
                available=max(evidence_times)
                basis=recovery_basis
            elif prior['availability_basis']==recovery_basis:
                # An unchanged reparse must retain the recovery floor rather
                # than alternate between acquisition and historic filing clocks.
                available=max(value for value in (available,prior['available_at']) if value)
                basis=recovery_basis
        kwargs={k:obs[k] for k in ('period_start','period_end','event_date','unit','currency') if obs.get(k) is not None}
        ids.append(ctx.store.put_record(obs['dataset'],key,data,source=evidence_source,raw_id=raw['raw_id'],
            company_id=resource.get('company_id'),security_id=resource.get('security_id'),
            published_at=meta.get('published_at'),publication_precision=meta.get('publication_precision','unknown'),
            publication_timezone=meta.get('publication_timezone'),available_at=available,availability_basis=basis,
            source_locator=locator,knowledge_type=obs.get('knowledge_type','company_statement'),
            quality=obs.get('quality','automatically_parsed_needs_verification'),parser_version=result.get('parser_version','unknown'),**kwargs))
    previous=ctx.store.db.execute('''SELECT r.* FROM records r WHERE source=? AND json_extract(data_json,'$.resource_key')=?
        AND sequence=(SELECT MAX(sequence) FROM records r2 WHERE r2.source=r.source AND r2.dataset=r.dataset AND r2.record_key=r.record_key)''',
        (evidence_source,resource['resource_key'])).fetchall()
    for old in previous:
        if (old['dataset'],old['record_key']) in produced:continue
        old_data=json.loads(old['data_json'])
        if old_data.get('status')=='not_emitted_by_current_parser' and old_data.get('source_content_sha256')==raw['sha256']:continue
        # A failed/missing current field must not silently leave an old numerical
        # answer looking current. This is parser status, NOT a factual retraction.
        if old_data.get('extraction_mode')!='automatic':continue
        if old_data.get('source_content_sha256')==raw['sha256']:
            ctx.store.validate_record(old['record_id'],verdict='rejected',raw_id=raw['raw_id'],
                reason='Same original bytes no longer emit this field under current parser; old extraction is not a reliable current fact.')
        tombstone={'status':'not_emitted_by_current_parser','value':None,'prior_record_id':old['record_id'],
                   'resource_key':resource['resource_key'],'source_content_sha256':raw['sha256'],
                   'reason':'Current source version missing or unsupported field; inspect parse issues',
                   'extraction_mode':'automatic','is_observation':False}
        ids.append(ctx.store.put_record(old['dataset'],old['record_key'],tombstone,source=evidence_source,raw_id=raw['raw_id'],
                   company_id=resource.get('company_id'),security_id=resource.get('security_id'),
                   available_at=raw['retrieved_at'],availability_basis='first_observed_parser_field_unavailable',
                   source_locator='current document parser coverage',quality='current_field_unavailable',
                   knowledge_type='system_inference',parser_version=result.get('parser_version','unknown')))
        tombstones+=1
    if tombstones:
        issues.append({'code':'PREVIOUS_FIELDS_NOT_EMITTED','message':f'{tombstones} previously emitted fields are unavailable in the current parse; prior numeric answers are not current observations.'})
    for issue in issues:
        ctx.gap(issue.get('code','PARSE_LIMITATION'),issue.get('message',str(issue)),source_id=resource['source_id'],
                company_id=resource.get('company_id'),data_type=issue.get('data_type',resource.get('data_type')),
                status='partial_parse',details={**issue,'resource_key':resource['resource_key'],'raw_id':raw['raw_id']})
    return {'status':'partial' if issues else 'parsed','record_ids':ids,'parsed_count':len(ids)-tombstones,
            'tombstone_count':tombstones,'rejected_count':rejected,'errors':issues,'coverage':result.get('coverage',{})}
