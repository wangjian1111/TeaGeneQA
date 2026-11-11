"""受控查询工具：每次关联都限定同一来源上下文。"""
from sqlalchemy import and_, func, or_, select

from .models import (
    ROW_MODELS, AnnotationRecord, BlastHit, DomainHit, EntityRecord,
    FactorAssignment, GeneLocus, GeneSequence, MappingMember, MappingRow,
    NetworkEdge, RawObservation, ReferenceContext, SourceFile,
)

LIMITATIONS = [
    '来源中的物种、组装版本、注释版本仍有待确认项；词法匹配不等于已确认生物学等价。',
    'GO/KO、BLASTP、PFAM/IPR 和 TF/TR 是数据库已有注释/分类，未提供实验验证证据。',
    'sampleID 的组织、处理、重复和 val 的单位/归一化未确认，不生成差异表达或因果解释。',
]


class QueryError(Exception):
    def __init__(self,status,code,message,details=None):
        self.status,self.code,self.message,self.details=status,code,message,details
        super().__init__(message)


def gene_record(conn,gene_id,context_key=None):
    query=select(GeneLocus.context_id,GeneLocus.source_entity_id,ReferenceContext.key,
                 ReferenceContext.label,ReferenceContext.metadata_raw).join(
                     ReferenceContext,ReferenceContext.id==GeneLocus.context_id).where(GeneLocus.css_id==gene_id)
    if context_key is not None:query=query.where(ReferenceContext.key==context_key)
    matches=conn.execute(query).mappings().all()
    if not matches:raise QueryError(404,'gene_not_found','在指定数据上下文中未检索到该标准 CSS ID；不能推断生物学上不存在。')
    if len(matches)>1:raise QueryError(409,'ambiguous_context','同一 CSS ID 存在于多个数据上下文，请选择 context_key。',
        [{'context_key':r['key'],'label':r['label'],'reference_metadata':r['metadata_raw']} for r in matches])
    found=dict(matches[0]);found['gene_id']=gene_id
    found['entity']=dict(conn.execute(select(EntityRecord).where(EntityRecord.context_id==found['context_id'],EntityRecord.source_entity_id==found['source_entity_id'])).mappings().one())
    return found


def provenance(row,source,context_key):
    return {'source_file_id':source['id'],'filename':source['filename'],'file_sha256':source['sha256'],
            'import_batch_id':source['batch_id'],'record_no':row['record_no'],'source_row_id':row['source_row_id'],
            'context_key':context_key,'record_url':f"/api/v1/sources/{source['id']}/records/{row['record_no']}"}


def gene_view(conn,gene):
    entity=gene['entity'];source=conn.execute(select(SourceFile).where(SourceFile.id==entity['source_file_id'])).mappings().one()
    return {'gene_id':gene['gene_id'],'context_key':gene['key'],'context_label':gene['label'],
            'reference_metadata':gene['metadata_raw'],'semantic_status':'pending',
            'raw_fields':entity['raw_fields'],'provenance':provenance(entity,source,gene['key'])}


def evidence_page(conn,gene,kind,limit=20,offset=0,*,sample_id=None,dataset_code=None,direction='outgoing',annotation_type=None):
    model=ROW_MODELS[kind]
    conditions=[model.context_id==gene['context_id']]
    if kind=='mapping':conditions.append(MappingRow.candidate_css_id==gene['gene_id'])
    elif kind=='network':
        left=NetworkEdge.tag1_raw==gene['gene_id'];right=NetworkEdge.tag2_raw==gene['gene_id']
        conditions.append({'outgoing':left,'incoming':right,'both':or_(left,right)}[direction])
        if dataset_code is not None:
            conditions.append(NetworkEdge.source_file_id.in_(select(SourceFile.id).where(SourceFile.context_id==gene['context_id'],SourceFile.dataset_code==dataset_code,SourceFile.kind=='network')))
    else:conditions.append(model.source_entity_id==gene['source_entity_id'])
    if sample_id is not None:conditions.append(RawObservation.sample_id_raw==sample_id)
    if annotation_type is not None:
        conditions.append((DomainHit.annotation_type if kind=='domain' else AnnotationRecord.anno_type)==annotation_type)
    total=conn.execute(select(func.count()).select_from(model).where(*conditions)).scalar_one()
    rows=conn.execute(select(model).where(*conditions).order_by(model.source_file_id,model.record_no).limit(limit).offset(offset)).mappings().all()
    sources={r['id']:r for r in conn.execute(select(SourceFile).where(SourceFile.id.in_({r['source_file_id'] for r in rows}))).mappings()}
    classifications={'annotation':'existing_computational_annotation','blast':'existing_sequence_similarity_hit',
        'domain':'existing_domain_annotation','factor':'existing_classification','expression':'raw_observation',
        'network':'existing_ordered_correlation_record','mapping':'source_mapping_candidate','sequence':'raw_sequence'}
    items=[]
    for row in rows:
        raw=dict(row['raw_fields'])
        if kind=='sequence':
            for field in ['Protein','Transcript','Gene','Upstream','Downstream','CDS']:raw[field]=row[field.lower()]
        items.append({'raw_fields':raw,'evidence_class':classifications[kind],
            'lexical_status':row.get('lexical_status','candidate_projection' if kind=='mapping' else 'matched'),
            'semantic_status':row.get('semantic_status','pending'),'provenance':provenance(row,sources[row['source_file_id']],gene['key'])})
    notes=list(LIMITATIONS)
    available=conn.execute(select(func.count()).select_from(SourceFile).where(SourceFile.context_id==gene['context_id'],SourceFile.kind==kind)).scalar_one()
    if not available:notes.append('当前上下文尚未导入此类来源文件；空结果不能解释为该基因缺少相应注释。')
    if kind=='network':notes.append('按指定方向返回原有序边；tag1 的 10 邻居导出不代表完整/对称网络，personR/personP 的方法仍待确认。')
    if kind=='mapping':notes.append('仅为原表同一行的标识符候选关系；数字后缀原样保留，不认定跨材料身份等价或正交关系。')
    return {'gene_id':gene['gene_id'],'context_key':gene['key'],'total':total,'limit':limit,'offset':offset,'items':items,'source_file_count':available,'limitations':notes}


def profile(conn,gene,limit=20,offset=0):
    sections={kind:evidence_page(conn,gene,kind,limit,offset) for kind in ['annotation','blast','domain','factor','expression','mapping','network']}
    sequence_count=conn.execute(select(func.count()).select_from(GeneSequence).where(GeneSequence.context_id==gene['context_id'],GeneSequence.source_entity_id==gene['source_entity_id'])).scalar_one()
    return {'gene':gene_view(conn,gene),'evidence':sections,'sequence_records':sequence_count,
            'experimental_validation_status':'not_provided','limitations':LIMITATIONS+['每类证据单独分页，total 是总量，items 仅为当前页；档案默认不加载长序列。']}


def source_record(conn,source_id,record_no):
    source=conn.execute(select(SourceFile).where(SourceFile.id==source_id)).mappings().first()
    if source is None:raise QueryError(404,'source_not_found','来源文件记录不存在')
    model=ROW_MODELS[source['kind']]
    row=conn.execute(select(model).where(model.source_file_id==source_id,model.record_no==record_no)).mappings().first()
    if row is None:raise QueryError(404,'record_not_found','该来源文件没有指定数据记录号')
    raw=dict(row['raw_fields'])
    if source['kind']=='sequence':
        for field in ['Protein','Transcript','Gene','Upstream','Downstream','CDS']:raw[field]=row[field.lower()]
    context=conn.execute(select(ReferenceContext.key).where(ReferenceContext.id==source['context_id'])).scalar_one()
    return {'raw_fields':raw,'evidence_class':'source_record','lexical_status':'not_applicable',
            'semantic_status':'pending','provenance':provenance(row,source,context)}


def mapping_lookup(conn,namespace,identifier,target_namespace=None,context_key=None,limit=20,offset=0):
    query=select(MappingRow,ReferenceContext.key).join(MappingMember,and_(
        MappingMember.source_file_id==MappingRow.source_file_id,MappingMember.record_no==MappingRow.record_no)).join(
        ReferenceContext,ReferenceContext.id==MappingRow.context_id).where(MappingMember.namespace_raw==namespace,MappingMember.identifier_raw==identifier)
    if context_key is not None:query=query.where(ReferenceContext.key==context_key)
    contexts=conn.execute(select(query.subquery().c.key).distinct()).scalars().all()
    if len(contexts)>1:raise QueryError(409,'ambiguous_context','该标识符在多个数据上下文中出现，请选择 context_key。',contexts)
    total=conn.execute(select(func.count()).select_from(query.subquery())).scalar_one()
    rows=conn.execute(query.order_by(MappingRow.source_file_id,MappingRow.record_no).limit(limit).offset(offset)).mappings().all()
    items=[]
    for row in rows:
        source=conn.execute(select(SourceFile).where(SourceFile.id==row['source_file_id'])).mappings().one()
        raw=row['raw_fields'];value=raw.get(target_namespace) if target_namespace else None
        items.append({'source_namespace':namespace,'source_identifier':identifier,'target_namespace':target_namespace,
            'target_identifier':None if value is None or value.strip().lower() in {'','na','n/a','null'} else value,
            'mapping_semantics':'source_row_cooccurrence_pending','raw_fields':raw,
            'provenance':provenance(row,source,row['key'])})
    return {'total':total,'limit':limit,'offset':offset,'ambiguous':total>1,'items':items,
            'limitations':['所有候选均保留；同一导出行共同出现不等于已确认正交、等价或同一生物学实体。']}
