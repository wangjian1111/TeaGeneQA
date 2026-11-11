#!/usr/bin/env python3
"""生成 50 条开发候选题和分层数据校验报告；专家终测模板独立留空。"""
import argparse
import hashlib
import json
import random
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.v11_oracle import Oracle, TABLES, build_cases, connect, validate_csv_references


def stratified_records(oracle,seed=20261110):
    from psycopg import sql
    rng=random.Random(seed);refs={};strata=[]
    def remember(label,rows,kind):
        for row in rows:refs[(row['source_file_id'],row['record_no'])]=(row,kind)
        strata.append({'name':label,'records':len(rows),'status':'checked' if rows else 'not_observed_in_snapshot','references':[[r['source_file_id'],r['record_no']] for r in rows]})
    for source in oracle.sources.values():
        n=source['row_count'];numbers=set()
        for third in range(3):
            low=n*third//3+1;high=n*(third+1)//3
            numbers.update(rng.sample(range(low,high+1),min(3,high-low+1)))
        query=sql.SQL('SELECT * FROM {} WHERE source_file_id=%s AND record_no=ANY(%s) ORDER BY record_no').format(sql.Identifier('teageneqa',TABLES[source['kind']]))
        remember(source['filename']+':按记录区段分层',oracle.conn.execute(query,(source['id'],list(numbers))).fetchall(),source['kind'])
    queries=[
        ('GO/KO 四类','annotation',"SELECT DISTINCT ON (anno_type) * FROM teageneqa.annotation_record WHERE context_id=%s ORDER BY anno_type,record_no"),
        ('BLAST 指标科学计数法','blast',"SELECT * FROM teageneqa.blast_hit WHERE context_id=%s AND raw_fields->>'pValue' ~ '[eE]' ORDER BY record_no LIMIT 8"),
        ('BLAST 原始零值','blast',"SELECT * FROM teageneqa.blast_hit WHERE context_id=%s AND raw_fields->>'pValue'='0' ORDER BY record_no LIMIT 5"),
        ('PFAM/IPR 方法分层','domain',"SELECT DISTINCT ON (annotation_type,method_raw) * FROM teageneqa.domain_hit WHERE context_id=%s ORDER BY annotation_type,method_raw,record_no"),
        ('PFAM/IPR 分值科学计数法','domain',"SELECT * FROM teageneqa.domain_hit WHERE context_id=%s AND raw_fields->>'eValue' ~ '[eE]' ORDER BY record_no LIMIT 5"),
        ('TF/TR 及原始特殊分类','factor',"SELECT DISTINCT ON (factor_type) * FROM teageneqa.factor_assignment WHERE context_id=%s ORDER BY factor_type,record_no"),
        ('原始观测零值','expression',"SELECT * FROM teageneqa.raw_observation WHERE context_id=%s AND value_numeric=0 ORDER BY record_no LIMIT 5"),
        ('原始观测非零值','expression',"SELECT * FROM teageneqa.raw_observation WHERE context_id=%s AND value_numeric>0 ORDER BY record_no LIMIT 5"),
        ('原始观测科学计数法','expression',"SELECT * FROM teageneqa.raw_observation WHERE context_id=%s AND value_raw ~ '[eE]' ORDER BY record_no LIMIT 5"),
        ('13 个非 CSS 实体','catalog',"SELECT * FROM teageneqa.entity_record WHERE context_id=%s AND entity_kind='non_css_unresolved' ORDER BY record_no"),
        ('非 CSS 原始观测','expression',"SELECT DISTINCT ON (source_entity_id) * FROM teageneqa.raw_observation WHERE context_id=%s AND measurement_kind='non_css_unresolved_measurement' ORDER BY source_entity_id,record_no"),
        ('目录描述缺失标记','catalog',"SELECT * FROM teageneqa.entity_record WHERE context_id=%s AND lower(raw_fields->>'productName') IN ('na','') ORDER BY record_no LIMIT 8"),
        ('SCZV2 非 .1 后缀','mapping',"SELECT DISTINCT ON (substring(raw_fields->>'SCZV2' from '[.][0-9]+$')) * FROM teageneqa.mapping_row WHERE context_id=%s AND raw_fields->>'SCZV2' !~ '[.]1$' ORDER BY substring(raw_fields->>'SCZV2' from '[.][0-9]+$'),record_no"),
        ('映射缺失 NA','mapping',"SELECT * FROM teageneqa.mapping_row WHERE context_id=%s AND lower(raw_fields->>'Biyun')='na' ORDER BY record_no LIMIT 8"),
    ]
    for label,kind,query in queries:remember(label,oracle.conn.execute(query,(oracle.cid,)).fetchall(),kind)
    groups=oracle.conn.execute('''SELECT raw_fields-'#'-'ID' AS business,count(*) AS n FROM teageneqa.domain_hit
        WHERE context_id=%s GROUP BY raw_fields-'#'-'ID' HAVING count(*)>1 ORDER BY min(record_no)''',(oracle.cid,)).fetchall()
    duplicate_report=[]
    for group in groups:
        from psycopg.types.json import Jsonb
        rows=oracle.conn.execute("SELECT * FROM teageneqa.domain_hit WHERE context_id=%s AND raw_fields-'#'-'ID'=%s ORDER BY record_no",(oracle.cid,Jsonb(group['business']))).fetchall()
        remember('合法业务重复：保留不同来源行',rows,'domain')
        duplicate_report.append({'business_fields':group['business'],'database_multiplicity':group['n'],'record_numbers':[r['record_no'] for r in rows]})
    reused=oracle.conn.execute('''SELECT identifier_raw,count(*) AS n FROM teageneqa.mapping_member WHERE namespace_raw='Biyun'
        AND source_file_id IN (SELECT id FROM teageneqa.source_file WHERE context_id=%s)
        GROUP BY identifier_raw HAVING count(*)>1 ORDER BY n DESC,identifier_raw LIMIT 1''',(oracle.cid,)).fetchone()
    rows=oracle.conn.execute('''SELECT r.* FROM teageneqa.mapping_row r JOIN teageneqa.mapping_member m USING(source_file_id,record_no)
        WHERE r.context_id=%s AND m.namespace_raw='Biyun' AND m.identifier_raw=%s ORDER BY r.record_no''',(oracle.cid,reused['identifier_raw'])).fetchall()
    remember('最大 Biyun 多候选组：逐候选校验',rows,'mapping')
    return refs,strata,duplicate_report,{'namespace':'Biyun','identifier':reused['identifier_raw'],'candidates':len(rows)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context-key',default='tpia_css_audit_snapshot_20261103')
    parser.add_argument('--output',type=Path,default=ROOT/'evaluations/v1_1')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    with connect() as conn:
        oracle=Oracle(conn,args.context_key);cases=build_cases(oracle)
        extra,strata,duplicates,multiple=stratified_records(oracle)
        quality=validate_csv_references(oracle,ROOT/'data/raw',extra)
        quality.update({'verified_at':datetime.now().astimezone().isoformat(),'context_key':oracle.key,'seed':20261110,
            'strata':strata,'duplicate_business_groups':duplicates,'multiple_candidate_group':multiple,
            'standard_css_count':conn.execute('SELECT count(*) AS n FROM teageneqa.gene_locus WHERE context_id=%s',(oracle.cid,)).fetchone()['n'],
            'non_css_count':conn.execute("SELECT count(*) AS n FROM teageneqa.entity_record WHERE context_id=%s AND entity_kind='non_css_unresolved'",(oracle.cid,)).fetchone()['n'],
            'scope':'字段保真和词法引用校验；不确认生物学语义。'})
    runtime=ROOT/'reports/v1_1_runtime';runtime.mkdir(parents=True,exist_ok=True)
    (runtime/'data_reliability.json').write_text(json.dumps(quality,ensure_ascii=False,indent=2),encoding='utf-8')
    if quality['status']!='passed':raise SystemExit('独立 CSV 校验失败，未生成候选答案。')
    target=args.output/'candidates.jsonl'
    content=''.join(json.dumps(c,ensure_ascii=False)+'\n' for c in cases)
    if target.exists() and target.read_text()!=content:
        # 版本化保存旧候选集，避免将已评测问题静默换成另一套题。
        old=hashlib.sha256(target.read_bytes()).hexdigest()[:12]
        target.rename(target.with_name('candidates.'+old+'.jsonl'))
    if not target.exists():target.write_text(content,encoding='utf-8')
    expert=args.output/'expert_test_template.jsonl'
    if not expert.exists():
        expert.write_text(''.join(json.dumps({'question_id':f'TGQA-E{i:03d}','question':'','intent':None,'expected_result':None,
            'supporting_evidence':[],'source_file':[],'source_record_id':[],'answerable':None,
            'biological_review_status':'awaiting_expert','reviewer':None,'reviewed_at':None,
            'split':'expert_final_holdout','usage':'不得用于提示词调优；审核、冻结后才用于最终评价。'},ensure_ascii=False)+'\n' for i in range(1,11)),encoding='utf-8')
    metadata={'generated_at':datetime.now().astimezone().isoformat(),'questions':len(cases),'category_counts':dict(Counter(c['category'] for c in cases)),
        'dataset_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'biological_review_status':'not_reviewed_candidate',
        'split_policy':'全部 50 题仅用于开发/回归和工程基线，不作为最终科研测试集；专家模板独立留空。',
        'oracle_dependencies':'只读 psycopg SQL、标准库 CSV 与哈希；没有导入 qa/services/models/importer。',
        'csv_validation':'reports/v1_1_runtime/data_reliability.json','source_context':args.context_key}
    (args.output/'manifest.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'questions':len(cases),'quality_status':quality['status'],'records_checked':quality['records_checked'],'strata':len(strata),'duplicate_groups':len(duplicates)},ensure_ascii=False))


if __name__=='__main__':main()
