import copy
import json
import uuid

import pytest
from sqlalchemy import func, select, text

from teageneqa.importer import ImportFailure, run_import
from teageneqa.models import AnnotationRecord, EntityRecord, ImportAttempt, MappingRow, SourceFile
from conftest import write_csv


def manifest_file(tmp_path,original,*,context=None,files=None,raw=None):
    data=copy.deepcopy(original)
    if context:data['context']['key']=context
    if files is not None:data['files']=files
    if raw:data['raw_dir']=str(raw)
    path=tmp_path/'manifest.json';path.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
    return path


def test_real_fixture_idempotent(engine,imported,tmp_path):
    with engine.connect() as conn:
        before=conn.execute(select(func.count()).select_from(SourceFile)).scalar_one()
        count=conn.execute(select(func.count()).select_from(EntityRecord)).scalar_one()
    result=run_import(engine,imported['path'],tmp_path/'report',chunk_size=3)
    assert result['status']=='completed'
    assert all(r['status']=='skipped' for r in result['files'])
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(SourceFile)).scalar_one()==before
        assert conn.execute(select(func.count()).select_from(EntityRecord)).scalar_one()==count


def test_conflicting_file_preserves_existing(engine,imported,tmp_path):
    raw=tmp_path/'raw';raw.mkdir();name=imported['files']['annotation']
    rows=copy.deepcopy(imported['rows']['annotation']);rows[0]['AnnoName']='测试冲突文本'
    write_csv(raw/name,rows)
    path=manifest_file(tmp_path,imported['manifest'],files=[{'path':name,'kind':'annotation'}],raw=raw)
    with engine.connect() as conn:before=conn.execute(select(func.count()).select_from(AnnotationRecord)).scalar_one()
    result=run_import(engine,path,tmp_path/'errors')
    assert result['files'][0]['code']=='snapshot_conflict'
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(AnnotationRecord)).scalar_one()==before
        assert conn.execute(select(func.count()).select_from(AnnotationRecord).where(AnnotationRecord.anno_name=='测试冲突文本')).scalar_one()==0


def test_file_rollback_and_resume(engine,imported,tmp_path):
    raw=tmp_path/'raw';raw.mkdir()
    cat=imported['files']['catalog'];ann=imported['files']['annotation']
    write_csv(raw/cat,imported['rows']['catalog'])
    valid=copy.deepcopy(imported['rows']['annotation'][:2]);broken=copy.deepcopy(valid);broken[1]['GeneID']='does_not_exist'
    write_csv(raw/ann,broken)
    context='rollback_'+uuid.uuid4().hex
    path=manifest_file(tmp_path,imported['manifest'],context=context,raw=raw,files=[{'path':cat,'kind':'catalog'},{'path':ann,'kind':'annotation'}])
    result=run_import(engine,path,tmp_path/'errors',chunk_size=1)
    assert result['status']=='failed' and result['files'][-1]['record_no']==2
    with engine.connect() as conn:
        context_id=conn.execute(text('SELECT id FROM teageneqa.reference_context WHERE key=:key'),{'key':context}).scalar_one()
        assert conn.execute(select(func.count()).select_from(AnnotationRecord).where(AnnotationRecord.context_id==context_id)).scalar_one()==0
        assert conn.execute(select(func.count()).select_from(SourceFile).where(SourceFile.context_id==context_id,SourceFile.kind=='annotation')).scalar_one()==0
    write_csv(raw/ann,valid)
    resumed=run_import(engine,path,tmp_path/'resume',chunk_size=1)
    assert [r['status'] for r in resumed['files']]==['skipped','imported']


@pytest.mark.parametrize('case',['missing_file','bad_header','duplicate_id','bad_number'])
def test_invalid_inputs_report_error(engine,imported,tmp_path,case):
    raw=tmp_path/'raw';raw.mkdir();kind='expression' if case=='bad_number' else 'annotation'
    filename=imported['files'][kind]
    rows=copy.deepcopy(imported['rows'][kind][:2])
    if case=='bad_header':
        for row in rows:row['wrong_field']=row.pop('ID')
    if case=='duplicate_id':rows[1]['ID']=rows[0]['ID']
    if case=='bad_number':rows[1]['val']='not-a-number'
    if case!='missing_file':write_csv(raw/filename,rows)
    # 使用一个新上下文并预先导入目录，防止“已有快照”掩盖待测异常。
    catalog=imported['files']['catalog'];write_csv(raw/catalog,imported['rows']['catalog'])
    path=manifest_file(tmp_path,imported['manifest'],context='invalid_'+uuid.uuid4().hex,raw=raw,
                       files=[{'path':catalog,'kind':'catalog'},{'path':filename,'kind':kind}])
    result=run_import(engine,path,tmp_path/'errors',chunk_size=1)
    assert result['status']=='failed'
    expected={'missing_file':'missing_file','bad_header':'schema_mismatch','duplicate_id':'23505','bad_number':'invalid_numeric'}
    assert result['files'][-1]['code']==expected[case],result
    if case in ['bad_number','duplicate_id']:assert result['files'][-1]['record_no']==2
    error_file=tmp_path/'errors'/f"batch_{result['batch_id']}_errors.jsonl"
    assert json.loads(error_file.read_text())['filename']==filename


def test_context_metadata_conflict(engine,imported,tmp_path):
    data=copy.deepcopy(imported['manifest']);data['context']['metadata']['assembly_version']='未经确认的新版本'
    path=tmp_path/'manifest.json';path.write_text(json.dumps(data),encoding='utf-8')
    with pytest.raises(ImportFailure,match='上下文元数据'):run_import(engine,path,tmp_path/'reports')


def test_encoding_and_delimiter(engine,imported,tmp_path):
    raw=tmp_path/'raw';raw.mkdir();rows=copy.deepcopy(imported['rows']['catalog'])
    rows[0]['productName']='保留中文原值，编码测试'
    filename=imported['files']['catalog']
    import csv
    with (raw/filename).open('w',encoding='gb18030',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]),delimiter='\t');writer.writeheader();writer.writerows(rows)
    path=manifest_file(tmp_path,imported['manifest'],context='encoding_'+uuid.uuid4().hex,raw=raw,files=[{'path':filename,'kind':'catalog'}])
    result=run_import(engine,path,tmp_path/'reports')
    assert result['status']=='completed'
    with engine.connect() as conn:
        row=conn.execute(select(SourceFile).where(SourceFile.id==result['files'][0]['source_file_id'])).mappings().one()
        assert row['encoding']=='gb18030' and row['delimiter']=='\t'


def test_output_cannot_write_into_raw(engine,imported):
    with pytest.raises(ValueError,match='raw_dir'):run_import(engine,imported['path'],imported['raw']/'reports')


def test_no_mapping_projection_without_explicit_anchor(engine,imported,tmp_path):
    manifest=copy.deepcopy(imported['manifest']);manifest['context']['key']='no_anchor_'+uuid.uuid4().hex
    manifest['context']['metadata'].pop('mapping_anchor_namespace')
    manifest['files']=[r for r in manifest['files'] if r['kind'] in {'catalog','mapping'}]
    path=tmp_path/'manifest.json';path.write_text(json.dumps(manifest),encoding='utf-8')
    result=run_import(engine,path,tmp_path/'reports')
    assert result['status']=='completed'
    source=result['files'][-1]['source_file_id']
    with engine.connect() as conn:
        count=conn.execute(select(func.count()).select_from(MappingRow).where(MappingRow.source_file_id==source)).scalar_one()
        projected=conn.execute(select(func.count()).select_from(MappingRow).where(MappingRow.source_file_id==source,MappingRow.candidate_css_id.is_not(None))).scalar_one()
        assert count==len(imported['rows']['mapping']) and projected==0
