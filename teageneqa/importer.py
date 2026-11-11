"""清单驱动的流式导入；按文件原子提交，重跑复用已成功的文件。"""
from __future__ import annotations

import argparse
import codecs
import csv
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from psycopg import sql
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import insert, select, text, update

from .db import SCHEMA, make_engine
from .models import (
    ROW_MODELS, Base, EntityRecord, GeneLocus, ImportAttempt, ImportBatch,
    MappingMember, NetworkDataset, ReferenceContext, SampleRecord, SourceFile,
)

csv.field_size_limit(sys.maxsize)
CSS = re.compile(r"^CSS[0-9]{7}$")
CSS_SUFFIX = re.compile(r"^(CSS[0-9]{7})\.([0-9]+)$")
MISSING = {"", "na", "n/a", "nan", "null", "none", "\\n"}
HEADERS = {
    "catalog": "# Genome GeneName ID chrom start end gType strand productName AACount exonCount exonStarts exonEnds fivePrimeUTRStart fivePrimeUTREnd threePrimeUTRStart threePrimeUTREnd".split(),
    "annotation": "# ID GeneID AnnoID AnnoName AnnoType".split(),
    "blast": "# ID geneID accession name sLen sStart sEnd mStart mLen mEnd mIdentity sIdentity pValue".split(),
    "domain": "# ID geneID accession name mStart mEnd eValue iType mothod".split(),
    "factor": "# id tag_id tf_type tf_name tf_desc".split(),
    "expression": "# ID geneID sampleID val".split(),
    "sequence": "# id chrom_tag_id Protein Transcript Gene Upstream Downstream CDS".split(),
    "network": "# ID tag1 tag2 personR personP".split(),
    "mapping": "# id SCZV2 Biyun DASZ HD LJ43 SCZV1 TGY YK10 COL CCH".split(),
}
SEQUENCE_FIELDS = ["Protein", "Transcript", "Gene", "Upstream", "Downstream", "CDS"]


class FileSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    kind: str
    dataset_code: str = ""
    expected_sha256: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")
    metadata: dict = Field(default_factory=dict)


class ContextSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")
    label: str = Field(min_length=1, max_length=300)
    metadata: dict = Field(default_factory=dict)


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format_version: int = Field(1, ge=1, le=1)
    context: ContextSpec
    raw_dir: str = "data/raw"
    files: list[FileSpec] = Field(min_length=1)


class ImportFailure(Exception):
    def __init__(self, code: str, message: str, record_no: int | None = None):
        self.code, self.record_no = code, record_no
        super().__init__(message)


def fingerprint(path: Path) -> tuple[str, int]:
    digest=hashlib.sha256();size=0
    with path.open('rb') as handle:
        while block:=handle.read(4*1024*1024):digest.update(block);size+=len(block)
    return digest.hexdigest(),size


def csv_format(path: Path) -> tuple[str,str,list[str]]:
    with path.open('rb') as handle:prefix=handle.read(131072)
    candidates=['utf-8-sig','gb18030']
    if prefix.startswith((codecs.BOM_UTF16_LE,codecs.BOM_UTF16_BE)):candidates=['utf-16']
    for encoding in candidates:
        try:
            # 初始片段可能全是 ASCII；全文件严格解码后再确定编码，避免晚出现的字符被误判。
            with path.open(encoding=encoding) as handle:
                while handle.read(1024*1024):pass
            sample=codecs.getincrementaldecoder(encoding)().decode(prefix,final=False)
            delimiter=max(',\t;|',key=lambda sep:len(next(csv.reader([sample.splitlines()[0]],delimiter=sep))))
            with path.open(encoding=encoding,newline='') as handle:header=next(csv.reader(handle,delimiter=delimiter,strict=True))
            return encoding,delimiter,header
        except (UnicodeError,csv.Error,IndexError,StopIteration):continue
    raise ImportFailure('encoding_or_header','无法无损解析编码或表头')


def copy_rows(conn, model, rows: list[dict]):
    if not rows:return
    # 表名/列名仅取自本地模型白名单，不接受请求或 CSV 的 SQL 片段。
    columns=list(rows[0]);table=model.__table__
    statement=sql.SQL('COPY {}.{} ({}) FROM STDIN').format(
        sql.Identifier(SCHEMA),sql.Identifier(table.name),
        sql.SQL(',').join(sql.Identifier(c) for c in columns))
    raw=conn.connection.driver_connection
    with raw.cursor() as cursor:
        with cursor.copy(statement) as writer:
            for row in rows:
                writer.write_row(tuple(Jsonb(row[c]) if isinstance(row[c],(dict,list)) else row[c] for c in columns))


def numeric(value: str, field: str):
    if value.strip().lower() in MISSING:return None
    try:
        number=Decimal(value)
        if not number.is_finite():raise InvalidOperation
        return number
    except (InvalidOperation,ValueError):
        raise ImportFailure('invalid_numeric',f'{field} 不是有限数值；原始字段未修改')


def transform(spec: FileSpec, row: dict, record: int, source_id: int, context_id: int, entities: dict, entity_names: set, mapping_anchor: str | None=None):
    row_id=row.get('ID',row.get('id',''))
    if not row_id or row_id.strip().lower() in MISSING:raise ImportFailure('missing_row_id','缺少来源行 ID')
    item={'source_file_id':source_id,'record_no':record,'context_id':context_id,
          'source_row_id':row_id,'raw_fields':dict(row)}
    kind=spec.kind;extra=[]
    if kind=='catalog':
        if not row['GeneName']:raise ImportFailure('missing_name','目录名称为空')
        standard=bool(CSS.fullmatch(row['GeneName']))
        item.update(source_entity_id=row_id,name_raw=row['GeneName'],
                    entity_kind='standard_css_candidate' if standard else 'non_css_unresolved',semantic_status='pending')
        if standard:extra.append((GeneLocus,{'context_id':context_id,'css_id':row['GeneName'],'source_entity_id':row_id,'semantic_status':'pending'}))
    elif kind=='network':
        for key in ['tag1','tag2']:
            if row[key] not in entity_names:raise ImportFailure('unknown_reference',f'{key} 在本上下文目录中无词法匹配')
        numeric(row['personR'],'personR');numeric(row['personP'],'personP')
        item.update(tag1_raw=row['tag1'],tag2_raw=row['tag2'],person_r_raw=row['personR'],person_p_raw=row['personP'])
    elif kind=='mapping':
        # 同字段结构不能证明映射锚点属于本参考上下文；必须由清单显式声明。
        match=CSS_SUFFIX.fullmatch(row.get(mapping_anchor,''));base=match.group(1) if match else None
        if base and base not in entity_names:base=None
        item.update(candidate_css_id=base,transform_rule=f'{mapping_anchor}:strip_numeric_suffix_candidate_only' if mapping_anchor else 'no_anchor_declared',semantic_status='pending')
        for namespace in HEADERS['mapping'][2:]:
            if row[namespace].strip().lower() not in MISSING:
                extra.append((MappingMember,{'source_file_id':source_id,'record_no':record,'namespace_raw':namespace,'identifier_raw':row[namespace]}))
    else:
        ref={'annotation':'GeneID','factor':'tag_id','sequence':'chrom_tag_id'}.get(kind,'geneID')
        entity=row[ref]
        if entity not in entities:raise ImportFailure('unknown_reference',f'{ref} 在本上下文目录中无词法匹配')
        item.update(source_entity_id=entity,lexical_status='matched',semantic_status='pending')
        if kind=='annotation':item.update(anno_id=row['AnnoID'],anno_type=row['AnnoType'],anno_name=row['AnnoName'])
        elif kind=='blast':item.update(accession=row['accession'])
        elif kind=='domain':item.update(accession=row['accession'],annotation_type=row['iType'],method_raw=row['mothod'])
        elif kind=='factor':item.update(factor_type=row['tf_type'],family_raw=row['tf_name'])
        elif kind=='expression':
            if not row['sampleID']:raise ImportFailure('missing_sample','sampleID 为空')
            item.update(sample_id_raw=row['sampleID'],value_raw=row['val'],value_numeric=numeric(row['val'],'val'),
                        measurement_kind='candidate_expression' if CSS.fullmatch(entities[entity]) else 'non_css_unresolved_measurement')
        elif kind=='sequence':
            for name in SEQUENCE_FIELDS:item[name.lower()]=row[name]
            item['raw_fields']={k:v for k,v in row.items() if k not in SEQUENCE_FIELDS}
    return item,extra


def import_file(engine, manifest: Manifest, spec: FileSpec, raw_dir: Path, batch: int, context: int, chunk_size: int):
    path=(raw_dir/spec.path).resolve()
    if not path.is_relative_to(raw_dir):raise ImportFailure('invalid_path','文件必须位于清单 raw_dir 内')
    if not path.is_file():raise ImportFailure('missing_file',f'文件不存在：{spec.path}')
    if spec.kind not in ROW_MODELS:raise ImportFailure('unknown_kind','清单数据类型不受支持')
    mapping_anchor=manifest.context.metadata.get('mapping_anchor_namespace')
    if spec.kind=='mapping' and mapping_anchor is not None and mapping_anchor not in HEADERS['mapping'][2:]:
        raise ImportFailure('invalid_mapping_anchor','清单映射锚点不是该来源表的命名空间')
    initial_stat=path.stat();sha,size=fingerprint(path)
    if spec.expected_sha256 and sha!=spec.expected_sha256:raise ImportFailure('hash_conflict','文件哈希与清单不符；保留数据库旧记录')
    encoding,delimiter,headers=csv_format(path)
    if len(headers)!=len(set(headers)) or set(headers)!=set(HEADERS[spec.kind]):
        raise ImportFailure('schema_mismatch',f'字段不匹配，期望 {HEADERS[spec.kind]}，实际 {headers}')
    with engine.begin() as conn:
        existing=conn.execute(select(SourceFile).where(SourceFile.context_id==context,SourceFile.kind==spec.kind,SourceFile.dataset_code==spec.dataset_code)).mappings().first()
        if existing:
            if existing['sha256']!=sha or existing['source_metadata']!=spec.metadata:
                raise ImportFailure('snapshot_conflict','同上下文、类型及数据集代码已有不同内容/元数据；请使用独立上下文，旧数据保留')
            return {'status':'skipped','source_file_id':existing['id'],'rows':existing['row_count'],'sha256':sha}
        source_id=conn.execute(insert(SourceFile).values(context_id=context,batch_id=batch,kind=spec.kind,
            dataset_code=spec.dataset_code,filename=spec.path,sha256=sha,size_bytes=size,encoding=encoding,
            delimiter=delimiter,headers=headers,row_count=0,source_metadata=spec.metadata).returning(SourceFile.id)).scalar_one()
        entities=dict(conn.execute(select(EntityRecord.source_entity_id,EntityRecord.name_raw).where(EntityRecord.context_id==context)).all())
        entity_names=set(entities.values())
        if spec.kind!='catalog' and not entities:raise ImportFailure('catalog_required','请先导入同上下文目录')
        if spec.kind=='network':conn.execute(insert(NetworkDataset).values(source_file_id=source_id,code_raw=spec.dataset_code,metadata_raw=spec.metadata,semantic_status='pending'))
        payload=[];auxiliary={};samples_seen=set();count=0;genome_codes=set()
        def flush():
            if not payload:return
            try:
                copy_rows(conn,ROW_MODELS[spec.kind],payload)
            except Exception as exc:
                diag=getattr(exc,'diag',None)
                match=re.search(r'line (\d+)',diag.context or '') if diag is not None else None
                if match:exc.record_no=payload[0]['record_no']+int(match.group(1))-1
                raise
            for model,rows in auxiliary.items():copy_rows(conn,model,rows)
            payload.clear();auxiliary.clear()
        with path.open(encoding=encoding,newline='') as handle:
            reader=csv.reader(handle,delimiter=delimiter,strict=True);next(reader)
            for record,values in enumerate(reader,1):
                count=record
                if len(values)!=len(headers):raise ImportFailure('row_width','记录列数与表头不同',record)
                row=dict(zip(headers,values))
                try:
                    if spec.kind=='catalog':
                        genome_codes.add(row['Genome'])
                        declared=manifest.context.metadata.get('source_genome_code_raw')
                        if len(genome_codes)>1 or (declared is not None and row['Genome']!=declared):
                            raise ImportFailure('mixed_reference','来源 Genome 代码与本上下文声明不一致，请拆分确认')
                    item,extra=transform(spec,row,record,source_id,context,entities,entity_names,mapping_anchor)
                    if spec.kind=='expression' and row['sampleID'] not in samples_seen:
                        conn.execute(insert(SampleRecord).values(source_file_id=source_id,sample_id_raw=row['sampleID'],metadata_raw={'tissue':None,'condition':None,'replicate':None,'unit':None,'normalization':None},semantic_status='pending'))
                        samples_seen.add(row['sampleID'])
                    payload.append(item)
                    for model,entry in extra:auxiliary.setdefault(model,[]).append(entry)
                    if len(payload)>=chunk_size:flush()
                except ImportFailure as exc:exc.record_no=record;raise
        flush()
        final_sha,final_size=fingerprint(path);after=path.stat()
        if (final_sha,final_size)!=(sha,size) or after.st_mtime_ns!=initial_stat.st_mtime_ns:
            raise ImportFailure('source_changed','读取期间源文件发生变化；本文件事务回滚')
        conn.execute(update(SourceFile).where(SourceFile.id==source_id).values(row_count=count))
    return {'status':'imported','source_file_id':source_id,'rows':count,'sha256':sha}


def run_import(engine, manifest_path: Path, output_dir: Path, chunk_size: int=2000):
    manifest_path=manifest_path.resolve();content=manifest_path.read_bytes()
    manifest=Manifest.model_validate_json(content)
    raw_dir=Path(manifest.raw_dir)
    if not raw_dir.is_absolute():raw_dir=(manifest_path.parent/raw_dir).resolve()
    else:raw_dir=raw_dir.resolve()
    output_dir=output_dir.resolve()
    if output_dir==raw_dir or output_dir.is_relative_to(raw_dir):raise ValueError('报告目录禁止位于 raw_dir 内')
    if chunk_size<1 or chunk_size>10000:raise ValueError('chunk_size 需在 1..10000')
    roles=[(s.kind,s.dataset_code) for s in manifest.files]
    if len(roles)!=len(set(roles)):raise ValueError('一个快照内同类型/数据集只能声明一个来源文件')
    output_dir.mkdir(parents=True,exist_ok=True)
    report={'context_key':manifest.context.key,'files':[],'status':'running'}
    with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as lock:
        lock_key='teageneqa:'+manifest.context.key
        if not lock.execute(text('SELECT pg_try_advisory_lock(hashtextextended(:key,0))'),{'key':lock_key}).scalar_one():
            raise ImportFailure('concurrent_import','该上下文已有导入在执行，请稍后重试')
        try:
            with engine.begin() as conn:
                existing=conn.execute(select(ReferenceContext).where(ReferenceContext.key==manifest.context.key)).mappings().first()
                if existing:
                    if existing['metadata_raw']!=manifest.context.metadata or existing['label']!=manifest.context.label:
                        raise ImportFailure('context_conflict','上下文元数据已存在且不同；请新建上下文，不覆盖旧版本')
                    context=existing['id']
                else:
                    context=conn.execute(insert(ReferenceContext).values(key=manifest.context.key,label=manifest.context.label,metadata_raw=manifest.context.metadata,semantic_status='pending').returning(ReferenceContext.id)).scalar_one()
                conn.execute(update(ImportBatch).where(ImportBatch.context_id==context,ImportBatch.status=='running').values(status='interrupted'))
                batch=conn.execute(insert(ImportBatch).values(context_id=context,manifest_sha256=hashlib.sha256(content).hexdigest(),status='running',summary={}).returning(ImportBatch.id)).scalar_one()
            report['batch_id']=batch
            for spec in sorted(manifest.files,key=lambda f:f.kind!='catalog'):
                with engine.begin() as conn:
                    attempt=conn.execute(insert(ImportAttempt).values(batch_id=batch,filename=spec.path,status='running',details={}).returning(ImportAttempt.id)).scalar_one()
                try:
                    result=import_file(engine,manifest,spec,raw_dir,batch,context,chunk_size)
                except Exception as exc:
                    # 不输出连接串/SQL/全序列。违反唯一键等数据库错误由结构化错误码报告。
                    result={'status':'failed','code':getattr(exc,'code',None) or getattr(exc,'sqlstate',None) or type(exc).__name__,
                            'message':str(exc) if isinstance(exc,(ImportFailure,UnicodeError,csv.Error)) else '数据库约束或读取失败，本文件事务已回滚',
                            'record_no':getattr(exc,'record_no',None)}
                    diag=getattr(exc,'diag',None)
                    if diag is not None:
                        result['constraint']=diag.constraint_name
                        match=re.search(r'line (\d+)',diag.context or '')
                        if match:result['copy_chunk_line']=int(match.group(1))
                result={'filename':spec.path,'kind':spec.kind,**result};report['files'].append(result)
                with engine.begin() as conn:conn.execute(update(ImportAttempt).where(ImportAttempt.id==attempt).values(status=result['status'],details=result))
                print(json.dumps(result,ensure_ascii=False),flush=True)
                if result['status']=='failed':break
            report['status']='failed' if any(r['status']=='failed' for r in report['files']) else 'completed'
            with engine.begin() as conn:conn.execute(update(ImportBatch).where(ImportBatch.id==batch).values(status=report['status'],summary=report,finished_at=datetime.now(timezone.utc)))
            (output_dir/f'batch_{batch}.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            with (output_dir/f'batch_{batch}_errors.jsonl').open('w',encoding='utf-8') as handle:
                for item in report['files']:
                    if item['status']=='failed':handle.write(json.dumps(item,ensure_ascii=False)+'\n')
            return report
        finally:lock.execute(text('SELECT pg_advisory_unlock(hashtextextended(:key,0))'),{'key':lock_key})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path,default=Path('config/import_manifest.json'))
    parser.add_argument('--output-dir',type=Path,default=Path('reports/v1_runtime/imports'))
    parser.add_argument('--chunk-size',type=int,default=2000)
    args=parser.parse_args();engine=make_engine()
    try:
        report=run_import(engine,args.manifest,args.output_dir,args.chunk_size)
    except (ImportFailure,ValueError) as exc:
        report={'status':'failed','code':getattr(exc,'code','invalid_manifest'),'message':str(exc)}
        # 清单级错误写到标准输出，避免无效/不安全的输出目录反而污染原数据。
        print(json.dumps(report,ensure_ascii=False))
    finally:engine.dispose()
    raise SystemExit(0 if report['status']=='completed' else 1)


if __name__=='__main__':main()
