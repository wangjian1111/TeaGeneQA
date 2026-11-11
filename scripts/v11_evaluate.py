#!/usr/bin/env python3
"""用冻结候选集独立检查结构化 API、规则问答和工具接口；不生成标准答案。"""
import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ROOT=Path(__file__).resolve().parents[1]


def compare_record(actual,expected):
    if actual.get('raw_fields')!=expected['raw_fields']:return False
    provenance=actual.get('provenance',{})
    return all(provenance.get(key)==expected[key] for key in ['source_file_id','record_no','source_row_id','filename']) and provenance.get('file_sha256')==expected['file_sha256']


def compare_data(kind,actual,expected):
    if kind=='basic':
        actual=actual.get('gene',actual)
        return actual.get('gene_id')==expected['gene_id'] and compare_record(actual,expected['entity'])
    if kind=='profile':
        return (compare_data('basic',actual.get('gene',{}),expected['gene'])
            and actual.get('experimental_validation_status')==expected['experimental_validation_status']
            and actual.get('sequence_records')==expected['sequence_records']
            and all(compare_data(k,actual.get('evidence',{}).get(k,{}),page) for k,page in expected['sections'].items()))
    if actual.get('total')!=expected['total']:return False
    if 'source_file_count' in expected and actual.get('source_file_count')!=expected['source_file_count']:return False
    if 'ambiguous' in expected and actual.get('ambiguous')!=expected['ambiguous']:return False
    items=actual.get('items',[])
    if len(items)!=len(expected['items']):return False
    for item,wanted in zip(items,expected['items']):
        if not compare_record(item,wanted):return False
        if 'target_identifier' in wanted and item.get('target_identifier')!=wanted['target_identifier']:return False
    return True


def score_response(case,response,layer):
    expected=case['expected_result'];kind=case['intent'];data=response.json();reasons=[]
    if kind=='unsupported':
        ok=response.status_code==200 and data.get('status') in {'unsupported','needs_clarification'} and data.get('result') is None
        return ok,[] if ok else ['未拒答/澄清超出现有证据范围的生物学问题']
    if response.status_code!=expected['http_status']:reasons.append('http_status_mismatch')
    elif response.status_code==404:
        err=data.get('error') or {}
        if err.get('code')!=expected['error_code']:reasons.append('error_code_mismatch')
    else:
        actual=data
        if layer=='rules':
            if data.get('intent')!=kind:reasons.append('intent_mismatch')
            actual=data.get('result') or {}
        elif layer=='tools':actual=data.get('data') or {}
        if not compare_data(kind,actual,expected):reasons.append('record_set_value_or_provenance_mismatch')
    return not reasons,reasons


def evaluate(base_url,dataset):
    cases=[json.loads(line) for line in dataset.read_text().splitlines() if line.strip()]
    names={'basic':'get_gene_profile','annotation':'get_gene_annotations','blast':'get_blast_hits','domain':'get_protein_domains',
           'factor':'get_transcription_factors','mapping':'resolve_gene_identifier','expression':'get_expression_values','profile':'get_gene_profile'}
    results={'structured':[],'rules':[],'tools':[]}
    with httpx.Client(base_url=base_url,timeout=30) as client:
        for case in cases:
            for layer in results:
                if case['intent']=='unsupported' and layer!='rules':continue
                if layer=='structured':response=client.get('/api/v1'+case['request']['path'],params=case['request']['params'])
                elif layer=='rules':response=client.post('/api/v1/query',json={'question':case['question'],'context_key':case['context_key'],'limit':5})
                else:
                    args=dict(case['request']['params']);args['gene_id']=case['gene_id']
                    if case['intent']=='mapping':args.pop('gene_id',None)
                    if 'sampleID' in args:args['sample_id']=args.pop('sampleID')
                    # 工具 profile 也遵循每类证据五条的同一分页预算。
                    args.setdefault('limit',5)
                    response=client.post('/api/v1/tools/'+names[case['intent']],json=args)
                passed,reasons=score_response(case,response,layer)
                data=response.json()
                results[layer].append({'question_id':case['question_id'],'category':case['category'],'expected_intent':case['intent'],
                    'actual_intent':data.get('intent'),'status_code':response.status_code,'passed':passed,'reasons':reasons,
                    'answer':data.get('answer') if layer=='rules' else None})
    summaries={}
    for layer,records in results.items():
        categories={}
        for cat in sorted({r['category'] for r in records}):
            subset=[r for r in records if r['category']==cat]
            categories[cat]={'total':len(subset),'passed':sum(r['passed'] for r in subset)}
        summaries[layer]={'total':len(records),'passed':sum(r['passed'] for r in records),
            'pass_rate':sum(r['passed'] for r in records)/len(records),'by_category':categories,
            'failure_reasons':dict(Counter(reason for r in records for reason in r['reasons']))}
    return {'evaluated_at':datetime.now().astimezone().isoformat(),'base_url':base_url,
        'dataset_sha256':hashlib.sha256(dataset.read_bytes()).hexdigest(),'candidate_only':True,'expert_reviewed':False,
        'summary':summaries,'results':results,'scope':'开发候选题上的工程基线；不是专家盲测或模型泛化指标。'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',default='http://127.0.0.1:18110')
    parser.add_argument('--dataset',type=Path,default=ROOT/'evaluations/v1_1/candidates.jsonl')
    parser.add_argument('--output',type=Path,default=ROOT/'reports/v1_1_runtime/qa_evaluation.json')
    args=parser.parse_args();u=urlsplit(args.base_url)
    if u.hostname not in {'127.0.0.1','localhost'}:raise SystemExit('只允许本机开发服务，不向远程发送数据。')
    report=evaluate(args.base_url,args.dataset)
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report['summary'],ensure_ascii=False))
    raise SystemExit(0 if all(report['summary'][k]['passed']==report['summary'][k]['total'] for k in ['structured','tools']) else 1)


if __name__=='__main__':main()
