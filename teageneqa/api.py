"""TeaGeneQA 只读 API；事实查询不依赖 LLM。"""
from contextlib import asynccontextmanager
import os
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from . import __version__
from .db import SCHEMA, make_engine
from .models import EntityRecord, GeneSequence, ReferenceContext, SourceFile
from .qa import answer_query
from .tools import invoke_tool, tool_catalog
from .schemas import EvidenceItem, EvidencePage, GeneView, HealthView, ProfileView, QueryRequest, QueryResponse
from .services import (
    LIMITATIONS, QueryError, evidence_page, gene_record, gene_view, mapping_lookup,
    profile, provenance, source_record,
)

CSSId=Annotated[str,Path(pattern=r'^CSS[0-9]{7}$')]
ContextKey=Annotated[str | None,Query(pattern=r'^[A-Za-z0-9_.-]{1,128}$')]
Limit=Annotated[int,Query(ge=1,le=200)]
Offset=Annotated[int,Query(ge=0,le=1_000_000)]
SampleId=Annotated[str | None,Query(alias='sampleID',pattern=r'^[A-Za-z0-9_.:-]{1,128}$')]
Namespace=Literal['SCZV2','Biyun','DASZ','HD','LJ43','SCZV1','TGY','YK10','COL','CCH']
SequenceType=Literal['Protein','Transcript','Gene','Upstream','Downstream','CDS']


def create_app(engine=None):
    engine=engine or make_engine()
    @asynccontextmanager
    async def lifespan(app):
        yield
        engine.dispose()
    def reject_unknown_parameters(request:Request):
        # 拼错 sampleID/版本参数不能静默退化成查全部，避免给研究者错误的过滤印象。
        route=request.scope.get('route')
        allowed={p.alias for p in route.dependant.query_params} if route is not None else set()
        unknown=sorted(set(request.query_params)-allowed)
        repeated=[key for key in request.query_params if len(request.query_params.getlist(key))>1]
        if unknown or repeated:raise QueryError(422,'invalid_parameters','存在未知或重复的查询参数。',{'unknown':unknown,'repeated':repeated})
    app=FastAPI(title='TeaGeneQA V1',version=__version__,description='查询已有来源记录；未确认的生物学元数据保持未决。',lifespan=lifespan,dependencies=[Depends(reject_unknown_parameters)])
    app.state.engine=engine
    if os.getenv('TEAGENEQA_TIMING')=='1':
        from .timing import enable_timing
        enable_timing(app,engine)
    def connection():
        with engine.connect() as conn:yield conn
    DB=Annotated[Connection,Depends(connection)]

    @app.exception_handler(QueryError)
    async def query_error(request,exc):
        return JSONResponse(status_code=exc.status,content={'error':{'code':exc.code,'message':exc.message,'details':exc.details}})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request,exc):
        details=[{'location':list(e['loc']),'message':e['msg'],'type':e['type']} for e in exc.errors()]
        return JSONResponse(status_code=422,content={'error':{'code':'invalid_parameters','message':'请求参数不符合约束。','details':details}})

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request,exc):
        return JSONResponse(status_code=503,content={'error':{'code':'database_unavailable','message':'数据库暂不可用或迁移未完成；请检查服务状态。','details':None}})

    @app.get('/api/v1/health',response_model=HealthView)
    def health():
        try:
            with engine.connect() as conn:
                conn.execute(text('SELECT 1'))
                revision=conn.execute(text(f'SELECT version_num FROM {SCHEMA}.alembic_version')).scalar_one_or_none()
                if not revision:raise RuntimeError('migration_missing')
            return {'status':'ok','service_version':__version__,'database':'postgresql','migration':revision}
        except (SQLAlchemyError,RuntimeError):
            return JSONResponse(status_code=503,content={'status':'degraded','service_version':__version__,'database':'unavailable_or_unmigrated','migration':None})

    @app.get('/api/v1/contexts')
    def contexts(conn:DB,limit:Limit=20,offset:Offset=0):
        rows=conn.execute(select(ReferenceContext).order_by(ReferenceContext.id).limit(limit).offset(offset)).mappings().all()
        return {'total':conn.execute(select(func.count()).select_from(ReferenceContext)).scalar_one(),'limit':limit,'offset':offset,'items':[dict(r) for r in rows]}

    @app.get('/api/v1/genes/{gene_id}',response_model=GeneView)
    def get_gene(gene_id:CSSId,conn:DB,context_key:ContextKey=None):
        return gene_view(conn,gene_record(conn,gene_id,context_key))

    @app.get('/api/v1/genes/{gene_id}/annotations',response_model=EvidencePage)
    def annotations(gene_id:CSSId,conn:DB,context_key:ContextKey=None,limit:Limit=20,offset:Offset=0,
                    annotation_type:Literal['Cellular Component','Molecular Function','Biological Process','Kegg Orthology'] | None=None):
        return evidence_page(conn,gene_record(conn,gene_id,context_key),'annotation',limit,offset,annotation_type=annotation_type)

    @app.get('/api/v1/genes/{gene_id}/blast',response_model=EvidencePage)
    def blast(gene_id:CSSId,conn:DB,context_key:ContextKey=None,limit:Limit=20,offset:Offset=0):
        return evidence_page(conn,gene_record(conn,gene_id,context_key),'blast',limit,offset)

    @app.get('/api/v1/genes/{gene_id}/domains',response_model=EvidencePage)
    def domains(gene_id:CSSId,conn:DB,context_key:ContextKey=None,limit:Limit=20,offset:Offset=0,annotation_type:Literal['pfam','ipr'] | None=None):
        return evidence_page(conn,gene_record(conn,gene_id,context_key),'domain',limit,offset,annotation_type=annotation_type)

    @app.get('/api/v1/genes/{gene_id}/expression',response_model=EvidencePage)
    def expression(gene_id:CSSId,conn:DB,context_key:ContextKey=None,limit:Limit=20,offset:Offset=0,sample_id:SampleId=None):
        return evidence_page(conn,gene_record(conn,gene_id,context_key),'expression',limit,offset,sample_id=sample_id)

    @app.get('/api/v1/genes/{gene_id}/factors',response_model=EvidencePage)
    def factors(gene_id:CSSId,conn:DB,context_key:ContextKey=None,limit:Limit=20,offset:Offset=0):
        return evidence_page(conn,gene_record(conn,gene_id,context_key),'factor',limit,offset)

    @app.get('/api/v1/genes/{gene_id}/mappings',response_model=EvidencePage)
    def mappings(gene_id:CSSId,conn:DB,context_key:ContextKey=None,limit:Limit=20,offset:Offset=0):
        return evidence_page(conn,gene_record(conn,gene_id,context_key),'mapping',limit,offset)

    @app.get('/api/v1/genes/{gene_id}/networks',response_model=EvidencePage)
    def networks(gene_id:CSSId,conn:DB,context_key:ContextKey=None,limit:Limit=20,offset:Offset=0,
                 dataset_code:Annotated[str | None,Query(pattern=r'^[A-Za-z0-9_.-]{1,128}$')]=None,direction:Literal['outgoing','incoming','both']='outgoing'):
        return evidence_page(conn,gene_record(conn,gene_id,context_key),'network',limit,offset,dataset_code=dataset_code,direction=direction)

    @app.get('/api/v1/genes/{gene_id}/profile',response_model=ProfileView)
    def get_profile(gene_id:CSSId,conn:DB,context_key:ContextKey=None,limit:Limit=20,offset:Offset=0):
        return profile(conn,gene_record(conn,gene_id,context_key),limit,offset)

    @app.get('/api/v1/genes/{gene_id}/sequences/{sequence_type}',response_model=EvidencePage)
    def sequence(gene_id:CSSId,sequence_type:SequenceType,conn:DB,context_key:ContextKey=None,limit:Limit=20,offset:Offset=0):
        gene=gene_record(conn,gene_id,context_key)
        conditions=[GeneSequence.context_id==gene['context_id'],GeneSequence.source_entity_id==gene['source_entity_id']]
        total=conn.execute(select(func.count()).select_from(GeneSequence).where(*conditions)).scalar_one()
        selected=getattr(GeneSequence,sequence_type.lower()).label('value')
        rows=conn.execute(select(GeneSequence.source_file_id,GeneSequence.record_no,GeneSequence.source_row_id,GeneSequence.raw_fields,selected).where(*conditions).order_by(GeneSequence.source_file_id,GeneSequence.record_no).limit(limit).offset(offset)).mappings().all()
        items=[]
        for row in rows:
            source=conn.execute(select(SourceFile).where(SourceFile.id==row['source_file_id'])).mappings().one()
            items.append({'raw_fields':{**row['raw_fields'],sequence_type:row['value']},'evidence_class':'raw_sequence','lexical_status':'matched','semantic_status':'pending','provenance':provenance(row,source,gene['key'])})
        available=conn.execute(select(func.count()).select_from(SourceFile).where(SourceFile.context_id==gene['context_id'],SourceFile.kind=='sequence')).scalar_one()
        return {'gene_id':gene_id,'context_key':gene['key'],'total':total,'limit':limit,'offset':offset,'items':items,'source_file_count':available,'limitations':['仅按需返回指定原始序列；未验证翻译、参考版本或所有异构体。']}

    @app.get('/api/v1/id-mappings')
    def id_mappings(conn:DB,namespace:Namespace,identifier:Annotated[str,Query(min_length=1,max_length=200)],
                    target_namespace:Namespace | None=None,context_key:ContextKey=None,limit:Limit=20,offset:Offset=0):
        return mapping_lookup(conn,namespace,identifier,target_namespace,context_key,limit,offset)

    @app.get('/api/v1/entities/non-css')
    def non_css(conn:DB,context_key:ContextKey=None,limit:Limit=20,offset:Offset=0):
        query=select(EntityRecord,ReferenceContext.key).join(ReferenceContext,ReferenceContext.id==EntityRecord.context_id).where(EntityRecord.entity_kind=='non_css_unresolved')
        if context_key:query=query.where(ReferenceContext.key==context_key)
        total=conn.execute(select(func.count()).select_from(query.subquery())).scalar_one()
        rows=conn.execute(query.order_by(EntityRecord.context_id,EntityRecord.record_no).limit(limit).offset(offset)).mappings().all()
        return {'total':total,'limit':limit,'offset':offset,'items':[{'source_entity_id':r['source_entity_id'],'name_raw':r['name_raw'],'context_key':r['key'],'entity_kind':r['entity_kind'],'semantic_status':r['semantic_status']} for r in rows]}

    @app.get('/api/v1/sources')
    def sources(conn:DB,context_key:ContextKey=None,limit:Limit=20,offset:Offset=0):
        query=select(SourceFile,ReferenceContext.key).join(ReferenceContext,ReferenceContext.id==SourceFile.context_id)
        if context_key:query=query.where(ReferenceContext.key==context_key)
        total=conn.execute(select(func.count()).select_from(query.subquery())).scalar_one()
        rows=conn.execute(query.order_by(SourceFile.id).limit(limit).offset(offset)).mappings().all()
        return {'total':total,'limit':limit,'offset':offset,'items':[dict(r) for r in rows]}

    @app.get('/api/v1/sources/{source_id}/records/{record_no}',response_model=EvidenceItem)
    def original_record(source_id:Annotated[int,Path(ge=1)],record_no:Annotated[int,Path(ge=1)],conn:DB):
        return source_record(conn,source_id,record_no)

    @app.post('/api/v1/query',response_model=QueryResponse)
    def query(request:QueryRequest,conn:DB):
        return answer_query(conn,request)

    @app.get('/api/v1/tools')
    def tools():
        return tool_catalog()

    @app.post('/api/v1/tools/{tool_name}')
    def call_tool(tool_name:Annotated[str,Path(pattern=r'^[a-z_]{1,64}$')],arguments:dict):
        status,body=invoke_tool(engine,tool_name,arguments)
        return JSONResponse(status_code=status,content=body)

    return app


app=create_app()
