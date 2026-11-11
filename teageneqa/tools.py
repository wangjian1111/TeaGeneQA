"""离线、只读的工具调用适配器。工具名和参数均受白名单限制，不接收 SQL。"""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from .models import SourceFile
from .schemas import EvidenceItem, EvidencePage, ProfileView
from .services import (
    QueryError, evidence_page, gene_record, mapping_lookup, profile, source_record,
)

Namespace = Literal['SCZV2','Biyun','DASZ','HD','LJ43','SCZV1','TGY','YK10','COL','CCH']


class Arguments(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class GeneArguments(Arguments):
    gene_id: str = Field(pattern=r'^CSS[0-9]{7}$')
    context_key: str | None = Field(None, pattern=r'^[A-Za-z0-9_.-]{1,128}$')
    limit: int = Field(20, ge=1, le=50)
    offset: int = Field(0, ge=0, le=1_000_000)


class AnnotationArguments(GeneArguments):
    annotation_type: Literal['Cellular Component','Molecular Function','Biological Process','Kegg Orthology'] | None = None


class DomainArguments(GeneArguments):
    annotation_type: Literal['pfam','ipr'] | None = None


class ExpressionArguments(GeneArguments):
    sample_id: str = Field(pattern=r'^[A-Za-z0-9_.:-]{1,128}$', description='原始 sampleID，不能用组织或处理名称代替。')


class IdentifierArguments(Arguments):
    namespace: Namespace
    identifier: str = Field(min_length=1, max_length=200)
    target_namespace: Namespace | None = None
    context_key: str | None = Field(None, pattern=r'^[A-Za-z0-9_.-]{1,128}$')
    limit: int = Field(20, ge=1, le=50)
    offset: int = Field(0, ge=0, le=1_000_000)


class SourceArguments(Arguments):
    source_file_id: int = Field(ge=1)
    record_no: int = Field(ge=1)


class MappingItem(BaseModel):
    source_namespace: str
    source_identifier: str
    target_namespace: str | None
    target_identifier: str | None
    mapping_semantics: str
    raw_fields: dict[str,str]
    provenance: dict[str,Any]


class MappingResult(BaseModel):
    total: int
    limit: int
    offset: int
    ambiguous: bool
    items: list[MappingItem]
    limitations: list[str]


class SourceResult(EvidenceItem):
    source_kind: str
    omitted_fields: list[dict[str,Any]] = Field(default_factory=list)


class ToolError(BaseModel):
    code: str
    message: str
    details: Any = None


class ToolResult(BaseModel):
    tool: str
    status: Literal['ok','empty','ambiguous','not_found','invalid_arguments','unavailable','unknown_tool','insufficient_evidence']
    data: dict[str,Any] | None
    error: ToolError | None = None
    evidence_policy: dict[str,Any]


TOOLS = {
    'get_gene_profile': (GeneArguments, ProfileView, '汇总已有多源记录；默认不返回长序列，不进行功能推断。'),
    'get_gene_annotations': (AnnotationArguments, EvidencePage, '查询已有 GO/KO 计算注释，不代表实验功能证明。'),
    'get_blast_hits': (GeneArguments, EvidencePage, '查询已有序列相似性命中，保留原始指标；不确证正交或功能。'),
    'get_protein_domains': (DomainArguments, EvidencePage, '查询 PFAM/IPR 原始注释，保留方法与合法重复记录。'),
    'get_expression_values': (ExpressionArguments, EvidencePage, '查询指定原始 sampleID 的 val；单位、组织和处理待确认。'),
    'resolve_gene_identifier': (IdentifierArguments, MappingResult, '返回指定命名空间的全部候选映射（分页）；不选择唯一跨材料实体。'),
    'get_source_evidence': (SourceArguments, SourceResult, '按文件和数据记录号回查原始证据；大字段省略并明确标记。'),
    'get_transcription_factors': (GeneArguments, EvidencePage, '查询来源中的 TF/TR 等分类，不推断调控关系。'),
}
POLICY = {
    'raw_facts': '原始字段/编号/观测值按来源返回，语义未确认时保持 pending。',
    'predicted_annotations': 'existing_computational_annotation / existing_domain_annotation / existing_classification',
    'homology_evidence': 'existing_sequence_similarity_hit，仅序列相似性命中。',
    'experimental_validation': {'status':'not_provided','items':[]},
    'inference': 'not_performed；无足够证据时不得生成生物学结论。',
}


def tool_catalog():
    return {'schema_version':'1.1','external_model_enabled':False,
            'result_envelope_schema':ToolResult.model_json_schema(),
            'tools':[{'type':'function','function':{'name':name,'description':description,
                'parameters':arguments.model_json_schema()},'data_schema':output.model_json_schema()}
                for name,(arguments,output,description) in TOOLS.items()]}


def invoke_tool(engine, name: str, arguments: dict):
    def result(code,status,data=None,error=None):
        return code,ToolResult(tool=name,status=status,data=data,error=error,evidence_policy=POLICY).model_dump(mode='json')
    if name not in TOOLS:
        return result(404,'unknown_tool',error={'code':'unknown_tool','message':'工具名不在白名单中。'})
    try:
        args=TOOLS[name][0].model_validate(arguments)
    except ValidationError as exc:
        # 不输出 input，避免无效参数携带凭据或长文本时被日志/响应复制。
        details=[{'location':list(e['loc']),'type':e['type']} for e in exc.errors()]
        return result(422,'invalid_arguments',error={'code':'invalid_arguments','message':'工具参数不符合约束。','details':details})
    try:
        with engine.connect() as conn:
            # 即使开发角色有写权限，本工具事务仍由 PostgreSQL 强制只读。
            conn.exec_driver_sql('SET TRANSACTION READ ONLY')
            if name=='resolve_gene_identifier':
                data=mapping_lookup(conn,args.namespace,args.identifier,args.target_namespace,args.context_key,args.limit,args.offset)
            elif name=='get_source_evidence':
                data=source_record(conn,args.source_file_id,args.record_no)
                kind=conn.execute(select(SourceFile.kind).where(SourceFile.id==args.source_file_id)).scalar_one()
                data['source_kind']=kind
                data['evidence_class']={'catalog':'raw_catalog_record','annotation':'existing_computational_annotation',
                    'blast':'existing_sequence_similarity_hit','domain':'existing_domain_annotation','factor':'existing_classification',
                    'expression':'raw_observation','sequence':'raw_sequence','network':'existing_ordered_correlation_record',
                    'mapping':'source_mapping_candidate'}[kind]
                data['omitted_fields']=[]
                for key,value in list(data['raw_fields'].items()):
                    if key in {'Protein','Transcript','Gene','Upstream','Downstream','CDS'} or len(value)>2000:
                        data['omitted_fields'].append({'field':key,'characters':len(value),'reason':'large_field_not_for_llm_context'})
                        del data['raw_fields'][key]
            else:
                gene=gene_record(conn,args.gene_id,args.context_key)
                if name=='get_gene_profile':data=profile(conn,gene,args.limit,args.offset)
                else:
                    kind={'get_gene_annotations':'annotation','get_blast_hits':'blast','get_protein_domains':'domain',
                          'get_expression_values':'expression','get_transcription_factors':'factor'}[name]
                    data=evidence_page(conn,gene,kind,args.limit,args.offset,
                        sample_id=getattr(args,'sample_id',None),annotation_type=getattr(args,'annotation_type',None))
            data=TOOLS[name][1].model_validate(data).model_dump(mode='json')
            if data.get('ambiguous'):status='ambiguous'
            elif data.get('source_file_count')==0:status='insufficient_evidence'
            elif data.get('total')==0:status='empty'
            else:status='ok'
            return result(200,status,data)
    except QueryError as exc:
        status='ambiguous' if exc.status==409 else 'not_found'
        return result(exc.status,status,error={'code':exc.code,'message':exc.message,'details':exc.details})
    except SQLAlchemyError:
        return result(503,'unavailable',error={'code':'database_unavailable','message':'数据库不可用或迁移未完成。'})
