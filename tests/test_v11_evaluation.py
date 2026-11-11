import ast
import copy
import json
from collections import Counter
from pathlib import Path

from scripts.v11_evaluate import compare_data, compare_record

ROOT=Path(__file__).resolve().parents[1]


def test_candidate_set_is_complete_and_not_expert_gold():
    cases=[json.loads(line) for line in (ROOT/'evaluations/v1_1/candidates.jsonl').read_text().splitlines()]
    required={'question_id','question','intent','expected_result','supporting_evidence','source_file','source_record_id','answerable','biological_review_status'}
    assert len(cases)==50 and len({c['question_id'] for c in cases})==50
    assert len({c['question'] for c in cases})==50
    assert len(Counter(c['category'] for c in cases))==10
    assert set(Counter(c['category'] for c in cases).values())=={5}
    assert all(required<=c.keys() and c['biological_review_status']=='not_reviewed_candidate' and c['split']=='development_candidate_only' for c in cases)
    experts=[json.loads(line) for line in (ROOT/'evaluations/v1_1/expert_test_template.jsonl').read_text().splitlines()]
    assert all(c['split']=='expert_final_holdout' for c in experts)
    assert not {c['question'] for c in cases}&{c['question'] for c in experts if c['question']}


def test_oracle_does_not_import_system_under_test():
    tree=ast.parse((ROOT/'scripts/v11_oracle.py').read_text())
    modules=[node.module or '' for node in ast.walk(tree) if isinstance(node,ast.ImportFrom)]
    assert not any(m in {'teageneqa.qa','teageneqa.services','teageneqa.models','teageneqa.importer','teageneqa.api'} for m in modules)


def test_evaluator_detects_changed_value_and_wrong_source():
    case=next(json.loads(line) for line in (ROOT/'evaluations/v1_1/candidates.jsonl').read_text().splitlines() if json.loads(line)['intent']=='basic')
    expected=case['expected_result']['entity']
    item={'raw_fields':copy.deepcopy(expected['raw_fields']),'provenance':{**expected,'file_sha256':expected['file_sha256']}}
    assert compare_record(item,expected)
    item['raw_fields']['GeneName']='wrong_gene'
    assert not compare_record(item,expected)
    item['raw_fields']=expected['raw_fields'];item['provenance']['record_no']+=1
    assert not compare_record(item,expected)
