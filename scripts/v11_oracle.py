"""独立事实基准：仅用显式 SQL 和 CSV 校验，不导入待测问答/服务/ORM 转换逻辑。"""
import csv
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from sqlalchemy.engine import make_url

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from teageneqa.db import database_url

csv.field_size_limit(sys.maxsize)
TABLES={'catalog':'entity_record','annotation':'annotation_record','blast':'blast_hit','domain':'domain_hit',
        'factor':'factor_assignment','expression':'raw_observation','sequence':'gene_sequence','network':'network_edge','mapping':'mapping_row'}
SEQUENCES=['Protein','Transcript','Gene','Upstream','Downstream','CDS']
NAMESPACES=['SCZV2','Biyun','DASZ','HD','LJ43','SCZV1','TGY','YK10','COL','CCH']
MISSING={'','na','n/a','nan','null','none','\\n'}


def connect(url=None):
    u=make_url(url or database_url())
    conn=psycopg.connect(host=u.host,port=u.port,dbname=u.database,user=u.username,password=u.password,
                         row_factory=dict_row,options='-csearch_path=public',connect_timeout=10)
    conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
    return conn


class Oracle:
    def __init__(self,conn,context_key):
        self.conn=conn;self.key=context_key
        self.context=conn.execute('SELECT * FROM teageneqa.reference_context WHERE key=%s',(context_key,)).fetchone()
        if not self.context:raise ValueError('指定来源上下文不存在')
        self.cid=self.context['id']
        self.sources={r['id']:r for r in conn.execute('SELECT * FROM teageneqa.source_file WHERE context_id=%s ORDER BY id',(self.cid,))}
        self.by_kind=defaultdict(list)
        for row in self.sources.values():self.by_kind[row['kind']].append(row)
        self.references={}

    def raw(self,row,kind):
        fields=dict(row['raw_fields'])
        if kind=='sequence':fields.update({field:row[field.lower()] for field in SEQUENCES})
        return fields

    def evidence(self,row,kind):
        source=self.sources[row['source_file_id']];raw=self.raw(row,kind)
        item={'raw_fields':raw,'source_file_id':source['id'],'filename':source['filename'],'file_sha256':source['sha256'],
              'record_no':row['record_no'],'source_row_id':row['source_row_id']}
        self.references[(source['id'],row['record_no'])]=(row,kind)
        return item

    def gene(self,css):
        return self.conn.execute('''SELECT e.* FROM teageneqa.gene_locus g JOIN teageneqa.entity_record e
            ON e.context_id=g.context_id AND e.source_entity_id=g.source_entity_id
            WHERE g.context_id=%s AND g.css_id=%s''',(self.cid,css)).fetchone()

    def page(self,css,kind,limit=5,offset=0,sample=None):
        entity=self.gene(css)
        if not entity:return {'http_status':404,'error_code':'gene_not_found'}
        table=sql.Identifier('teageneqa',TABLES[kind])
        if kind=='mapping':condition=sql.SQL('candidate_css_id=%s');value=css
        elif kind=='network':condition=sql.SQL('tag1_raw=%s');value=css
        else:condition=sql.SQL('source_entity_id=%s');value=entity['source_entity_id']
        query=sql.SQL('SELECT * FROM {} WHERE context_id=%s AND {}').format(table,condition)
        params=[self.cid,value]
        if sample is not None:query+=sql.SQL(' AND sample_id_raw=%s');params.append(sample)
        rows=self.conn.execute(query+sql.SQL(' ORDER BY source_file_id,record_no'),params).fetchall()
        return {'http_status':200,'total':len(rows),'source_file_count':len(self.by_kind[kind]),
                'items':[self.evidence(r,kind) for r in rows[offset:offset+limit]]}

    def expected(self,kind,css=None,params=None):
        params=params or {}
        if kind=='basic':
            row=self.gene(css)
            return {'http_status':200,'gene_id':css,'entity':self.evidence(row,'catalog')} if row else {'http_status':404,'error_code':'gene_not_found'}
        if kind=='profile':
            return {'http_status':200,'gene':self.expected('basic',css),
                'sections':{k:self.page(css,k) for k in ['annotation','blast','domain','factor','expression','mapping','network']},
                'sequence_records':self.page(css,'sequence',limit=0)['total'],'experimental_validation_status':'not_provided'}
        if kind=='mapping':
            rows=self.conn.execute('''SELECT r.* FROM teageneqa.mapping_row r JOIN teageneqa.mapping_member m
                ON m.source_file_id=r.source_file_id AND m.record_no=r.record_no
                WHERE r.context_id=%s AND m.namespace_raw=%s AND m.identifier_raw=%s ORDER BY r.source_file_id,r.record_no''',
                (self.cid,params['namespace'],params['identifier'])).fetchall()
            items=[]
            for row in rows[:5]:
                item=self.evidence(row,'mapping');target=row['raw_fields'][params['target_namespace']]
                item['target_identifier']=None if target.strip().lower() in MISSING else target
                items.append(item)
            return {'http_status':200,'total':len(rows),'ambiguous':len(rows)>1,'items':items}
        return self.page(css,kind,sample=params.get('sampleID'))


def flatten_evidence(expected):
    if 'entity' in expected:return [expected['entity']]
    if 'sections' in expected:return flatten_evidence(expected['gene'])+[e for p in expected['sections'].values() for e in p['items']]
    return expected.get('items',[])


def build_cases(oracle):
    cases=[];used=set()
    def select_genes(table=None,missing=False,minimum=1):
        if table:
            comparison='NOT EXISTS' if missing else 'EXISTS'
            clause=f"AND {comparison} (SELECT 1 FROM teageneqa.{TABLES[table]} t WHERE t.context_id=g.context_id AND t.source_entity_id=g.source_entity_id GROUP BY t.source_entity_id HAVING count(*)>={minimum})"
        else:clause=''
        rows=oracle.conn.execute('SELECT g.css_id FROM teageneqa.gene_locus g WHERE g.context_id=%s '+clause+' ORDER BY g.css_id',(oracle.cid,))
        selected=[]
        for row in rows:
            if row['css_id'] not in used:
                selected.append(row['css_id']);used.add(row['css_id'])
            if len(selected)==5:break
        if len(selected)!=5:raise ValueError('真实数据不足以生成指定分层的五条问题')
        return selected
    def add(category,question,kind,css=None,params=None,answerable=True,reason=None):
        params=params or {};expected=oracle.expected(kind,css,params) if kind!='unsupported' else {
            'http_status':200,'required_behavior':'refuse_or_clarify','reason':reason,'experimental_validation_status':'not_provided'}
        refs=flatten_evidence(expected)
        files=sorted({e['filename'] for e in refs})
        # 缺失/不可答问题没有虚构的证据行；登记被检查的来源范围与判定方法。
        if not files:files=sorted(s['filename'] for s in oracle.sources.values())
        path={'basic':f'/genes/{css}','annotation':f'/genes/{css}/annotations','blast':f'/genes/{css}/blast',
              'domain':f'/genes/{css}/domains','factor':f'/genes/{css}/factors','expression':f'/genes/{css}/expression',
              'profile':f'/genes/{css}/profile','mapping':'/id-mappings','unsupported':None}[kind]
        supporting=[{k:v for k,v in e.items() if k!='raw_fields'} for e in refs]
        cases.append({'question_id':f'TGQA-C{len(cases)+1:03d}','question':question,'category':category,'intent':kind,
            'gene_id':css,'context_key':oracle.key,'request':{'method':'GET','path':path,'params':{'context_key':oracle.key,'limit':5,**params} if kind!='basic' else {'context_key':oracle.key}},
            'expected_result':expected,'supporting_evidence':supporting,
            'source_file':files,'source_record_id':[{'source_file_id':e['source_file_id'],'record_no':e['record_no'],'source_row_id':e['source_row_id']} for e in supporting],
            'answerable':answerable,'biological_review_status':'not_reviewed_candidate',
            'split':'development_candidate_only','absence_or_scope_reason':reason,
            'oracle_method':'independent_parameterized_sql_plus_csv_record_and_file_hash_validation'})
    templates={
        'basic':['查询 {g} 的基本信息','{g} 的原始坐标和链方向是什么？','查看 {g} 的已有产物描述','请给出 {g} 的目录信息及来源','{g} 的基本信息中有哪些原始字段？'],
        'annotation':['查询 {g} 的 GO/KO 注释','{g} 有哪些已有功能注释记录？','查看 {g} 的 GO 和 KO 注释及原始编号','请列出 {g} 的 GO/KO 注释来源','{g} 的 GO/KO 注释共有多少条？'],
        'blast':['查询 {g} 的 BLASTP 命中','{g} 已有的同源比对记录有哪些？','查看 {g} 的 BLASTP accession 和原始指标','列出 {g} 的 BLASTP 比对来源','{g} 的 BLASTP 命中有多少条？'],
        'domain':['查询 {g} 的 PFAM/IPR 结构域','查看 {g} 的 PFAM 与 IPR 原始记录','{g} 的结构域注释有哪些方法？','列出 {g} 的 PFAM/IPR 证据来源','{g} 的 PFAM/IPR 记录有多少条？'],
        'factor':['查询 {g} 的 TF/TR 分类','{g} 的转录因子家族记录是什么？','查看 {g} 的 TF/TR 原始分类描述','列出 {g} 的转录因子分类来源','{g} 有多少条 TF/TR 分类记录？'],
        'expression':['查询 {g} 在 sampleID={s} 的原始数值','查看 {g} 在样本编号 {s} 的原始 val','{g} 在 sampleID={s} 的表达原始记录是什么？','查询 {g} 在 sampleID={s} 的原始观测及来源','{g} 在 sampleID={s} 的 val 原字符串是什么？'],
        'profile':['汇总 {g} 的多来源证据','查询 {g} 的综合证据档案','{g} 的注释、比对和观测证据分别有多少条？','查看 {g} 的多源功能注释汇总及来源','请汇总 {g} 的现有证据，并区分实验验证状态'],
    }
    for kind in ['basic','annotation','blast','domain','factor']:
        for i,g in enumerate(select_genes(None if kind=='basic' else kind,minimum=2 if kind in {'blast','domain'} else 1)):
            add(kind,templates[kind][i].format(g=g),kind,g)
    mappings=oracle.conn.execute('''SELECT namespace_raw,identifier_raw,count(*) AS n FROM teageneqa.mapping_member
        WHERE source_file_id IN (SELECT id FROM teageneqa.source_file WHERE context_id=%s) AND namespace_raw='Biyun'
        GROUP BY namespace_raw,identifier_raw HAVING count(*)>1 ORDER BY n DESC,identifier_raw LIMIT 3''',(oracle.cid,)).fetchall()
    suffix_rows=oracle.conn.execute("SELECT raw_fields->>'SCZV2' AS identifier FROM teageneqa.mapping_row WHERE context_id=%s AND raw_fields->>'SCZV2' !~ '\\.1$' ORDER BY record_no LIMIT 2",(oracle.cid,)).fetchall()
    for r in mappings:
        p={'namespace':r['namespace_raw'],'identifier':r['identifier_raw'],'target_namespace':'SCZV2'}
        add('mapping',f"将 {p['namespace']} 编号 {p['identifier']} 映射到 SCZV2，请保留全部候选。",'mapping',params=p)
    for r in suffix_rows:
        p={'namespace':'SCZV2','identifier':r['identifier'],'target_namespace':'LJ43'}
        add('mapping',f"查询 SCZV2 编号 {r['identifier']} 对应的 LJ43 原始编号，保留数字后缀。",'mapping',params=p)
    for i,g in enumerate(select_genes('expression')):
        row=oracle.conn.execute('SELECT sample_id_raw FROM teageneqa.raw_observation WHERE context_id=%s AND source_entity_id=%s ORDER BY record_no OFFSET %s LIMIT 1',
                                (oracle.cid,oracle.gene(g)['source_entity_id'],i)).fetchone()
        add('expression',templates['expression'][i].format(g=g,s=row['sample_id_raw']),'expression',g,{'sampleID':row['sample_id_raw']})
    for i,g in enumerate(select_genes()):add('profile',templates['profile'][i].format(g=g),'profile',g)
    for kind,label in [('annotation','GO/KO 注释'),('blast','BLASTP 命中'),('domain','PFAM/IPR 注释')]:
        g=select_genes(kind,missing=True)[0]
        add('no_data',f'查询 {g} 的{label}，若无记录请说明快照限制。',kind,g,reason='该上下文该类来源已导入，但该基因记录数为零。')
    g=select_genes()[0]
    sample='absent_sample_v11'
    assert not oracle.conn.execute('SELECT 1 FROM teageneqa.raw_observation WHERE context_id=%s AND sample_id_raw=%s LIMIT 1',(oracle.cid,sample)).fetchone()
    add('no_data',f'查询 {g} 在 sampleID={sample} 的原始数值。','expression',g,{'sampleID':sample},reason='指定原始样本编号在快照中无记录。')
    absent='CSS9999999';assert oracle.gene(absent) is None
    add('no_data',f'查询 {absent} 的基本信息。','basic',absent,answerable=False,reason='指定标准形式 ID 未出现在本地目录；不推断生物学不存在。')
    biology=[('是否在干旱处理中显著上调？','缺少样本、对照、重复和统计结果。'),('是否有实验证明它决定茶树抗病？','未提供茶树实验验证证据。'),
             ('是否因果调控儿茶素合成？','已有注释与相关不能证明因果。'),('在哪个组织表达最高？','缺少 sampleID 到组织及可比测量定义。'),
             ('的 GO 注释能否证明它控制儿茶素合成？','GO 计算注释不能单独证明实验功能或因果。')]
    for g,(q,reason) in zip(select_genes(),biology):add('unsupported_biology',g+q,'unsupported',g,answerable=False,reason=reason)
    assert len(cases)==50 and all(n==5 for n in Counter(c['category'] for c in cases).values())
    return cases


def validate_csv_references(oracle,raw_dir,extra=None):
    """整文件流式读取、全量计数；只保留需要检查的记录，序列不整表载入内存。"""
    refs=dict(oracle.references);refs.update(extra or {});files=[];checks=[]
    for source in oracle.sources.values():
        path=raw_dir/source['filename'];before=path.stat()
        with path.open('rb') as handle:sha=hashlib.file_digest(handle,'sha256').hexdigest()
        wanted={n for sid,n in refs if sid==source['id']};seen=set();count=0
        with path.open(encoding=source['encoding'],newline='') as handle:
            reader=csv.DictReader(handle,delimiter=source['delimiter']);headers=reader.fieldnames
            for number,row in enumerate(reader,1):
                count=number
                if number not in wanted:continue
                actual,kind=refs[(source['id'],number)];fields=oracle.raw(actual,kind)
                match=fields==row;typed=True
                field_map={'catalog':{'source_entity_id':'ID','name_raw':'GeneName'},'annotation':{'source_entity_id':'GeneID','anno_id':'AnnoID','anno_type':'AnnoType','anno_name':'AnnoName'},
                    'blast':{'source_entity_id':'geneID','accession':'accession'},'domain':{'source_entity_id':'geneID','accession':'accession','annotation_type':'iType','method_raw':'mothod'},
                    'factor':{'source_entity_id':'tag_id','factor_type':'tf_type','family_raw':'tf_name'},'expression':{'source_entity_id':'geneID','sample_id_raw':'sampleID','value_raw':'val'},
                    'sequence':{'source_entity_id':'chrom_tag_id'},'network':{'tag1_raw':'tag1','tag2_raw':'tag2','person_r_raw':'personR','person_p_raw':'personP'}}
                typed=all(actual[k]==row[v] for k,v in field_map.get(kind,{}).items())
                if kind=='expression':typed=typed and actual['value_numeric']==Decimal(row['val'])
                if kind=='mapping':
                    members=oracle.conn.execute('SELECT namespace_raw,identifier_raw FROM teageneqa.mapping_member WHERE source_file_id=%s AND record_no=%s',(source['id'],number)).fetchall()
                    typed={m['namespace_raw']:m['identifier_raw'] for m in members}=={k:row[k] for k in NAMESPACES if row[k].strip().lower() not in MISSING}
                    anchor=oracle.context['metadata_raw'].get('mapping_anchor_namespace')
                    match_base=re.fullmatch(r'(CSS[0-9]{7})\.[0-9]+',row.get(anchor,'')) if anchor else None
                    candidate=match_base.group(1) if match_base and oracle.gene(match_base.group(1)) else None
                    typed=typed and actual['candidate_css_id']==candidate and actual['semantic_status']=='pending'
                row_id=row.get('ID',row.get('id'))
                provenance_ok=actual['source_file_id']==source['id'] and actual['record_no']==number and actual['source_row_id']==row_id
                checks.append({'source_file':source['filename'],'source_file_id':source['id'],'record_no':number,'source_row_id':row_id,
                    'raw_fields_equal':match,'typed_fields_equal':bool(typed),'provenance_equal':provenance_ok})
                seen.add(number)
        after=path.stat()
        db_count=oracle.conn.execute(sql.SQL('SELECT count(*) AS n FROM {} WHERE source_file_id=%s').format(sql.Identifier('teageneqa',TABLES[source['kind']])),(source['id'],)).fetchone()['n']
        files.append({'filename':source['filename'],'csv_rows':count,'database_rows':db_count,'registry_rows':source['row_count'],
            'count_matches':count==db_count==source['row_count'],'hash_matches':sha==source['sha256'],
            'headers_match':headers==source['headers'],'file_unchanged':before.st_size==after.st_size==source['size_bytes'] and before.st_mtime_ns==after.st_mtime_ns,
            'requested_records':len(wanted),'checked_records':len(seen),'all_requested_found':seen==wanted})
    passed=all(all(f[k] for k in ['count_matches','hash_matches','headers_match','file_unchanged','all_requested_found']) for f in files) and all(c['raw_fields_equal'] and c['typed_fields_equal'] and c['provenance_equal'] for c in checks)
    return {'status':'passed' if passed else 'failed','files':files,'record_checks':checks,'records_checked':len(checks),'csv_total_rows':sum(f['csv_rows'] for f in files)}
