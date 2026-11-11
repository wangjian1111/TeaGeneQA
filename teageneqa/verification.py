"""只读核对真实 CSV 与 PostgreSQL 的数量、抽样原字段和原文件完整性。"""
import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select, text

from .db import SCHEMA, make_engine
from .importer import HEADERS, Manifest, SEQUENCE_FIELDS, fingerprint
from .models import ROW_MODELS, EntityRecord, GeneLocus, MappingMember, ReferenceContext, SourceFile


def verify_manifest(engine,manifest_path):
    manifest_path=Path(manifest_path).resolve()
    manifest=Manifest.model_validate_json(manifest_path.read_bytes())
    raw=Path(manifest.raw_dir)
    if not raw.is_absolute():raw=(manifest_path.parent/raw).resolve()
    result={'verified_at':datetime.now().astimezone().isoformat(),'context_key':manifest.context.key,'files':[],'checks':{}}
    with engine.connect() as conn:
        context=conn.execute(select(ReferenceContext).where(ReferenceContext.key==manifest.context.key)).mappings().one()
        expected_members=0
        for spec in manifest.files:
            source=conn.execute(select(SourceFile).where(SourceFile.context_id==context['id'],SourceFile.kind==spec.kind,SourceFile.dataset_code==spec.dataset_code)).mappings().one()
            model=ROW_MODELS[spec.kind];path=raw/spec.path
            before=path.stat();sha,size=fingerprint(path)
            sample_at={1,max(1,source['row_count']//2),source['row_count']};samples={};rows=0
            with path.open(encoding=source['encoding'],newline='') as handle:
                reader=csv.DictReader(handle,delimiter=source['delimiter'])
                for n,row in enumerate(reader,1):
                    rows=n
                    if n in sample_at:samples[n]=row
                    if spec.kind=='mapping':expected_members+=sum(row[k].strip().lower() not in {'','na','n/a','nan','null','none','\\n'} for k in HEADERS['mapping'][2:])
            db_count=conn.execute(select(func.count()).select_from(model).where(model.source_file_id==source['id'])).scalar_one()
            sample_results=[]
            for number,row in samples.items():
                actual=conn.execute(select(model).where(model.source_file_id==source['id'],model.record_no==number)).mappings().one()
                fields=dict(actual['raw_fields'])
                if spec.kind=='sequence':
                    for field in SEQUENCE_FIELDS:fields[field]=actual[field.lower()]
                sample_results.append({'record_no':number,'raw_fields_equal':fields==row})
            after=path.stat()
            entry={'filename':spec.path,'kind':spec.kind,'source_file_id':source['id'],'csv_rows':rows,'source_registry_rows':source['row_count'],
                'database_rows':db_count,'count_matches':rows==source['row_count']==db_count,
                'file_sha256':sha,'hash_matches':sha==source['sha256'] and (spec.expected_sha256 is None or sha==spec.expected_sha256),
                'size_matches':size==source['size_bytes'],'stat_unchanged':before.st_mtime_ns==after.st_mtime_ns and before.st_size==after.st_size,
                'sampled_records':sample_results}
            result['files'].append(entry)
        result['csv_total_rows']=sum(r['csv_rows'] for r in result['files'])
        result['database_total_rows']=sum(r['database_rows'] for r in result['files'])
        result['entity_counts']=dict(conn.execute(select(EntityRecord.entity_kind,func.count()).where(EntityRecord.context_id==context['id']).group_by(EntityRecord.entity_kind)).all())
        result['standard_css_loci']=conn.execute(select(func.count()).select_from(GeneLocus).where(GeneLocus.context_id==context['id'])).scalar_one()
        actual_members=conn.execute(select(func.count()).select_from(MappingMember).join(SourceFile,SourceFile.id==MappingMember.source_file_id).where(SourceFile.context_id==context['id'])).scalar_one()
        result['mapping_members']={'expected_nonmissing_cells':expected_members,'database_rows':actual_members}
        result['migration']=conn.execute(text(f'SELECT version_num FROM {SCHEMA}.alembic_version')).scalar_one()
        result['database_version']=conn.execute(text('SHOW server_version')).scalar_one()
        result['database_bytes']=conn.execute(text('SELECT pg_database_size(current_database())')).scalar_one()
        result['checks']={
            'all_file_counts_match':all(r['count_matches'] for r in result['files']),
            'all_file_hashes_match':all(r['hash_matches'] for r in result['files']),
            'all_file_sizes_match':all(r['size_matches'] for r in result['files']),
            'raw_stats_unchanged':all(r['stat_unchanged'] for r in result['files']),
            'sampled_raw_fields_match':all(s['raw_fields_equal'] for r in result['files'] for s in r['sampled_records']),
            'mapping_members_preserved':expected_members==actual_members,
            'standard_loci_are_separate':result['standard_css_loci']==result['entity_counts'].get('standard_css_candidate',0),
            'context_metadata_pending':context['semantic_status']=='pending' and context['metadata_raw']==manifest.context.metadata,
        }
    result['status']='passed' if all(result['checks'].values()) else 'failed'
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path,default=Path('config/import_manifest.json'))
    parser.add_argument('--output',type=Path,default=Path('reports/v1_runtime/data_verification.json'))
    args=parser.parse_args();engine=make_engine()
    try:result=verify_manifest(engine,args.manifest)
    finally:engine.dispose()
    output=args.output.resolve()
    manifest=Manifest.model_validate_json(args.manifest.read_bytes());raw=(args.manifest.resolve().parent/manifest.raw_dir).resolve()
    if output.is_relative_to(raw):raise ValueError('禁止写入原始目录')
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':result['status'],'csv_rows':result['csv_total_rows'],'database_rows':result['database_total_rows'],'checks':result['checks']},ensure_ascii=False))
    raise SystemExit(0 if result['status']=='passed' else 1)


if __name__=='__main__':main()
