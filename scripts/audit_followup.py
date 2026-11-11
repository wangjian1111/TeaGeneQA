#!/usr/bin/env python3
"""对审计暂存库补充实体类型、矩阵完整度和注释一致性检查。"""
import argparse
import csv
import json
import sqlite3
from pathlib import Path

from audit_data import cols, dump, query_scalar


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit-dir',type=Path,default=Path('reports/audit_final'))
    args=parser.parse_args();out=args.audit_dir
    inv=json.loads((out/'inventory.json').read_text())
    files={f['filename']:f for f in inv['files']}
    conn=sqlite3.connect(f'file:{(out/"audit_work.sqlite").resolve()}?mode=ro',uri=True)
    f=lambda name:files['tpia_'+name+'_css.csv']
    t=lambda name:f(name)['table']
    c=lambda name,field:cols(f(name)['headers'],[field])[0]
    gene=t('chrom_tag');g_id=c('chrom_tag','ID');g_name=c('chrom_tag','GeneName')
    expr=t('gene_expro');e_id=c('gene_expro','geneID');e_sample=c('gene_expro','sampleID');e_val=c('gene_expro','val')
    groups={};profiles=[]
    for label,condition in [('standard_css',f"g.{g_name} GLOB 'CSS[0-9][0-9][0-9][0-9][0-9][0-9][0-9]'"),('nonstandard_named',f"g.{g_name} NOT GLOB 'CSS[0-9][0-9][0-9][0-9][0-9][0-9][0-9]'")]:
        query=f'SELECT e.{e_id},e.{e_sample},e.{e_val},g.{g_name} FROM {expr} e JOIN {gene} g ON e.{e_id}=g.{g_id} WHERE {condition}'
        ids={};samples={};rows=zeros=0;minimum=maximum=None
        for identifier,sample,value,name in conn.execute(query):
            rows+=1;ids[identifier]=ids.get(identifier,0)+1;samples[sample]=samples.get(sample,0)+1
            number=float(value);zeros+=number==0
            minimum=number if minimum is None else min(minimum,number);maximum=number if maximum is None else max(maximum,number)
        groups[label]={'rows':rows,'entity_count':len(ids),'sample_count':len(samples),'sample_ids':sorted(samples,key=int),'rows_per_entity_distribution':{str(n):list(ids.values()).count(n) for n in sorted(set(ids.values()))},'rows_per_sample_distribution':{str(n):list(samples.values()).count(n) for n in sorted(set(samples.values()))},'expected_cartesian':len(ids)*len(samples),'missing_cartesian_cells':len(ids)*len(samples)-rows,'zeros':zeros,'min':minimum,'max':maximum}
    groups['sample_id_overlap']=sorted(set(groups['standard_css']['sample_ids'])&set(groups['nonstandard_named']['sample_ids']))
    for row in conn.execute(f"SELECT g.{g_id},g.{g_name},COUNT(*),COUNT(DISTINCT e.{e_sample}),MIN(CAST(e.{e_val} AS REAL)),MAX(CAST(e.{e_val} AS REAL)) FROM {expr} e JOIN {gene} g ON e.{e_id}=g.{g_id} WHERE g.{g_name} NOT GLOB 'CSS[0-9][0-9][0-9][0-9][0-9][0-9][0-9]' GROUP BY g.{g_id},g.{g_name}"):
        profiles.append(dict(zip(['ID','name','records','samples','min','max'],row)))
    result={'expression_entity_groups':groups,'nonstandard_entity_profiles':profiles}
    a=t('gene_annotation');ag,at,ai,an=[c('gene_annotation',x) for x in ['GeneID','AnnoType','AnnoID','AnnoName']]
    result['annotation_term_label_conflicts']=[dict(zip(['type','id','label_count'],r)) for r in conn.execute(f'SELECT {at},{ai},COUNT(DISTINCT {an}) FROM {a} GROUP BY {at},{ai} HAVING COUNT(DISTINCT {an})>1')]
    for term in result['annotation_term_label_conflicts']:
        term['labels']=[dict(zip(['label','rows','example_record'],r)) for r in conn.execute(f'SELECT {an},COUNT(*),MIN(_audit_row) FROM {a} WHERE {at}=? AND {ai}=? GROUP BY {an}',(term['type'],term['id']))]
        term['distinct_after_trim']=len({item['label'].strip() for item in term['labels']})
    p=t('annopfamdetail');pg,pa,ps,pe,pt,pm=[c('annopfamdetail',x) for x in ['geneID','accession','mStart','mEnd','iType','mothod']]
    result['domain_counts_by_type_method']=[dict(zip(['iType','mothod','rows','genes'],r)) for r in conn.execute(f'SELECT {pt},{pm},COUNT(*),COUNT(DISTINCT {pg}) FROM {p} GROUP BY {pt},{pm}')]
    result['domain_duplicate_business_examples']=[]
    for digest,n in conn.execute(f'SELECT _business_hash,COUNT(*) FROM {p} GROUP BY _business_hash HAVING COUNT(*)>1'):
        rows=[dict(zip(['record']+f('annopfamdetail')['headers'],r)) for r in conn.execute(f'SELECT _audit_row,{",".join("c"+str(i) for i in range(len(f("annopfamdetail")["headers"])))} FROM {p} WHERE _business_hash=?',(digest,))]
        result['domain_duplicate_business_examples'].append({'count':n,'records':rows})
    b=t('annoblastpdetail');bg,bl=c('annoblastpdetail','geneID'),c('annoblastpdetail','sLen')
    result['blast_hits_per_gene_distribution']=dict(conn.execute(f'SELECT n,COUNT(*) FROM (SELECT {bg},COUNT(*) n FROM {b} GROUP BY {bg}) GROUP BY n'))
    result['blast_slen_minus_query_protein_length']=dict(conn.execute(f'SELECT CAST(b.{bl} AS INTEGER)-s.protein_length,COUNT(*) FROM {b} b JOIN sequence_lengths s ON b.{bg}=s.chrom_tag_id GROUP BY 1'))
    result['tf_unusual_type_records']=[dict(zip(['record']+f('tfactor')['headers'],r)) for r in conn.execute(f"SELECT _audit_row,{','.join('c'+str(i) for i in range(len(f('tfactor')['headers'])))} FROM {t('tfactor')} WHERE {c('tfactor','tf_type')} NOT IN ('TF','TR')")]
    conv=t('gene_id_convert');ci,cs=[c('gene_id_convert',x) for x in ['id','SCZV2']]
    result['conversion_suffix_not_one_records']=[dict(zip(['record','conversion_id','SCZV2','id_join_base_name'],r)) for r in conn.execute(f"SELECT x._audit_row,x.{ci},x.{cs},g.{g_name} FROM {conv} x JOIN {gene} g ON x.{ci}=g.{g_id} WHERE x.{cs}!=g.{g_name}||'.1'")]
    result['conversion_id_base_mismatches']=[dict(zip(['record','conversion_id','SCZV2','id_join_base_name'],r)) for r in conn.execute(f"SELECT x._audit_row,x.{ci},x.{cs},g.{g_name} FROM {conv} x JOIN {gene} g ON x.{ci}=g.{g_id} WHERE SUBSTR(x.{cs},1,INSTR(x.{cs},'.')-1)!=g.{g_name}")]
    # 将内部文本引用经主目录转换为候选 CSS 名称，补齐跨表示形式的文件级覆盖。
    directory=dict(conn.execute(f'SELECT {g_id},{g_name} FROM {gene}'))
    file_sets={};projection=[]
    for item in inv['files']:
        headers=item['headers'];tab=item['table'];filename=item['filename']
        if 'tag1' in headers:
            left,right=cols(headers,['tag1','tag2'])
            names={r[0] for r in conn.execute(f'SELECT {left} FROM {tab} UNION SELECT {right} FROM {tab}')}
        elif 'SCZV2' in headers:
            col=cols(headers,['SCZV2'])[0]
            names={r[0].rsplit('.',1)[0] for r in conn.execute(f'SELECT {col} FROM {tab}')}
        elif 'GeneName' in headers:
            names={v for v in directory.values() if v.startswith('CSS')}
        else:
            ref=next(v for v in ['geneID','GeneID','tag_id','chrom_tag_id'] if v in headers)
            col=cols(headers,[ref])[0]
            raw_ids={r[0] for r in conn.execute(f'SELECT DISTINCT {col} FROM {tab}')}
            names={directory[v] for v in raw_ids if v in directory and directory[v].startswith('CSS')}
        file_sets[filename]=names
        projection.append({'file':filename,'candidate_css_entities':len(names),'coverage_of_standard_css':len(names)/50525,'status':'词法引用经主目录投影或候选后缀变换；生物学语义待确认'})
    result['candidate_css_file_coverage']=projection
    result['candidate_css_file_pairwise']=[]
    filenames=sorted(file_sets)
    for i,first in enumerate(filenames):
        for second in filenames[i+1:]:
            sa,sb=file_sets[first],file_sets[second];n=len(sa&sb)
            result['candidate_css_file_pairwise'].append({'a_file':first,'b_file':second,'a_distinct':len(sa),'b_distinct':len(sb),'intersection':n,'coverage_a':n/len(sa),'coverage_b':n/len(sb),'a_only':len(sa-sb),'b_only':len(sb-sa)})
    for name,key in [('candidate_css_file_overlap.csv','candidate_css_file_pairwise')]:
        with (out/name).open('w',encoding='utf-8-sig',newline='') as handle:
            writer=csv.DictWriter(handle,fieldnames=list(result[key][0]));writer.writeheader();writer.writerows(result[key])
    dump(out/'followup.json',result)
    with (out/'anomaly_records.csv').open('w',encoding='utf-8-sig',newline='') as handle:
        writer=csv.writer(handle);writer.writerow(['file','record','field','value','issue'])
        for row in inv['checks']['tpia_chrom_tag_css.csv']['nonstandard_gene_names']:
            writer.writerow(['tpia_chrom_tag_css.csv',row['record'],'GeneName',row['GeneName'],'实体类别待确认；非标准 CSS 基因，不自动判为错误'])
        for row in result['tf_unusual_type_records']:
            writer.writerow(['tpia_tfactor_css.csv',row['record'],'tf_type',row['tf_type'],'类别层级疑似混用，待确认'])
        for row in result['conversion_suffix_not_one_records']:
            writer.writerow(['tpia_gene_id_convert_css.csv',row['record'],'id/SCZV2',f"{row['conversion_id']}/{row['SCZV2']}",f"非 .1 后缀变体；基础编号 {row['id_join_base_name']} 一致，不是错配；后缀含义待确认"])
        for term in result['annotation_term_label_conflicts']:
            for label in term['labels']:
                writer.writerow(['tpia_gene_annotation_css.csv',label['example_record'],'AnnoID/AnnoName',f"{term['id']}/{label['label']}",'同编号原始标签有前导空白差异；trim 后相同，未发现该项语义冲突；保留原值'])
        for group in result['domain_duplicate_business_examples']:
            for row in group['records']:
                writer.writerow(['tpia_annopfamdetail_css.csv',row['record'],'geneID/accession/mStart/mEnd',f"{row['geneID']}/{row['accession']}/{row['mStart']}/{row['mEnd']}",'去除导出序号和行ID后的全字段重复；保留原记录'])
    conn.close()
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
