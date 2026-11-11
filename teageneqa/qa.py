"""无外部模型的白名单意图解析；复用与 API 相同的查询工具。"""
import re

from .schemas import QueryRequest
from .services import LIMITATIONS, evidence_page, gene_record, gene_view

GENE_PATTERN=re.compile(r'(?<![A-Za-z0-9_])CSS[0-9]{7}(?:\.[0-9]+)?(?![A-Za-z0-9_])')
UNSUPPORTED=re.compile(r'上调|下调|差异表达|显著|调控|因果|抗病|决定|实验证明|证明.*功能|TPM|FPKM|\b(?:DROP|ALTER|DELETE|INSERT|SELECT|UPDATE)\b',re.I)


def answer_query(conn,request: QueryRequest):
    question=request.question.strip()
    if UNSUPPORTED.search(question):
        return {'status':'unsupported','answer':'当前数据仅支持已有记录查询，不能据此回答差异表达、单位、调控因果或实验证明。请改为查询具体记录。','limitations':LIMITATIONS}
    genes=list(dict.fromkeys(GENE_PATTERN.findall(question)))
    if len(genes)!=1 or '.' in genes[0]:
        return {'status':'needs_clarification','answer':'请提供一个完整的标准 CSS 位点 ID（如 CSS0000001）。带数字后缀的原编号请先使用 ID 映射查询。','limitations':LIMITATIONS}
    lowered=question.lower();sample=None
    if any(x in lowered for x in ['pfam','ipr','结构域']):kind='domain'
    elif any(x in lowered for x in ['blast','比对','同源']):kind='blast'
    elif any(x in lowered for x in ['go','kegg','ko','注释']):kind='annotation'
    elif any(x in lowered for x in ['表达','sample','原始数值','val','样本']):
        kind='expression'
        match=re.search(r'(?:sampleid|sample_id|样本编号|样本)\s*[=:：]?\s*([A-Za-z0-9_.-]+)',question,re.I)
        if not match:return {'status':'needs_clarification','answer':'请明确 sampleID；当前不能将组织或处理名称自动转换成样本编号。','limitations':LIMITATIONS}
        sample=match.group(1)
    elif any(x in lowered for x in ['基本','信息','位置','描述','查询','查看']):kind='basic'
    else:return {'status':'unsupported','answer':'当前支持基本信息、GO/KO、PFAM/IPR、BLASTP 和指定 sampleID 的原始数值查询。','limitations':LIMITATIONS}
    gene=gene_record(conn,genes[0],request.context_key)
    if kind=='basic':
        result=gene_view(conn,gene);raw=result['raw_fields']
        message=f"{genes[0]} 的已有目录记录：chrom={raw['chrom']}，start={raw['start']}，end={raw['end']}，strand={raw['strand']}；原始产物描述：{raw['productName']}。"
    else:
        result=evidence_page(conn,gene,kind,request.limit,request.offset,sample_id=sample)
        label={'domain':'PFAM/IPR','blast':'BLASTP 命中','annotation':'GO/KO 注释','expression':'原始观测'}[kind]
        message=f"{genes[0]} 的 {label}共 {result['total']} 条，当前返回 {len(result['items'])} 条（offset={request.offset}）。"
        if kind=='expression':
            values=[item['raw_fields']['val'] for item in result['items']]
            message+=f" sampleID={sample}，当前页原始 val={values}；单位及样本含义待确认。"
        if not result['source_file_count']:message+='当前上下文尚未导入此类来源文件，不能解释为该基因缺少相应记录。'
        elif not result['total']:message+='本数据快照未检索到记录；不能据此判断生物学上不存在。'
    return {'status':'ok','intent':kind,'answer':message,'result':result,'limitations':LIMITATIONS}
