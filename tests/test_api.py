import copy
import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from teageneqa.api import create_app
from teageneqa.db import make_engine
from teageneqa.importer import run_import
from teageneqa.models import EntityRecord, GeneLocus

CTX={'context_key':'real_fixture'}


def all_pages(client,path,params=None):
    items=[];offset=0
    while True:
        response=client.get(path,params={**CTX,**(params or {}),'limit':2,'offset':offset})
        assert response.status_code==200,response.text
        data=response.json();items.extend(data['items']);offset+=len(data['items'])
        if offset>=data['total']:return items,data['total']
        assert data['items']


def test_standard_css_lookup(client,imported):
    actual=client.get('/api/v1/genes/CSS0000001',params=CTX)
    expected=next(r for r in imported['rows']['catalog'] if r['GeneName']=='CSS0000001')
    assert actual.status_code==200
    assert actual.json()['raw_fields']==expected
    assert actual.json()['reference_metadata']['assembly_version'] is None
    assert actual.json()['semantic_status']=='pending'


def test_nonexistent_css(client):
    response=client.get('/api/v1/genes/CSS9999999',params=CTX)
    assert response.status_code==404 and response.json()['error']['code']=='gene_not_found'


def test_multiple_blast_complete(client,imported):
    expected=[r for r in imported['rows']['blast'] if r['geneID']=='37220']
    items,total=all_pages(client,'/api/v1/genes/CSS0037220/blast')
    assert len(expected)>1 and total==len(expected)
    assert [item['raw_fields'] for item in items]==expected


def test_domain_multimethod_and_business_duplicates(client,imported):
    expected=[r for r in imported['rows']['domain'] if r['geneID']=='8782']
    items,total=all_pages(client,'/api/v1/genes/CSS0008782/domains')
    assert total==len(expected) and [r['raw_fields'] for r in items]==expected
    assert {'pfam','ipr'} <= {r['iType'] for r in expected}
    assert len({r['mothod'] for r in expected})>1
    assert sum(r['ID'] in {'227002','227003'} for r in expected)==2
    filtered=client.get('/api/v1/genes/CSS0008782/domains',params={**CTX,'annotation_type':'pfam','limit':200}).json()
    assert filtered['total']==sum(r['iType']=='pfam' for r in expected)


def test_go_ko_fields_preserved(client,imported):
    expected=[r for r in imported['rows']['annotation'] if r['GeneID']=='173']
    items,total=all_pages(client,'/api/v1/genes/CSS0000173/annotations')
    assert total==len(expected) and [r['raw_fields'] for r in items]==expected
    ko=client.get('/api/v1/genes/CSS0027431/annotations',params={**CTX,'annotation_type':'Kegg Orthology'}).json()
    assert ko['total']>0 and all(r['raw_fields']['AnnoID'].startswith('K') for r in ko['items'])


def test_reverse_mapping_ambiguity(client,imported):
    query={'namespace':'Biyun','identifier':'GWHTAMJR007835','target_namespace':'SCZV2'}
    expected=[r for r in imported['rows']['mapping'] if r['Biyun']==query['identifier']]
    items,total=all_pages(client,'/api/v1/id-mappings',query)
    assert total==len(expected)>1
    assert [r['target_identifier'] for r in items]==[r['SCZV2'] for r in expected]
    assert client.get('/api/v1/id-mappings',params={**CTX,**query}).json()['ambiguous'] is True


def test_non_one_suffix_preserved(client,imported):
    row=next(r for r in imported['rows']['mapping'] if r['SCZV2'].endswith('.2'))
    response=client.get('/api/v1/id-mappings',params={**CTX,'namespace':'SCZV2','identifier':row['SCZV2'],'target_namespace':'LJ43'})
    assert response.status_code==200
    assert response.json()['items'][0]['raw_fields']==row
    assert client.get('/api/v1/genes/'+row['SCZV2'],params=CTX).status_code==422
    projected=client.get('/api/v1/genes/'+row['SCZV2'].rsplit('.',1)[0]+'/mappings',params=CTX).json()
    assert projected['items'][0]['raw_fields']['SCZV2']==row['SCZV2']


def test_all_13_non_css_entities_isolated(client,engine,imported):
    response=client.get('/api/v1/entities/non-css',params={**CTX,'limit':200}).json()
    expected=[r for r in imported['rows']['catalog'] if not r['GeneName'].startswith('CSS')]
    assert response['total']==len(expected)==13
    assert {r['name_raw'] for r in response['items']}=={r['GeneName'] for r in expected}
    for name in ['Theanine','Caffeine','EGCG']:
        assert client.get('/api/v1/genes/'+name,params=CTX).status_code==422
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(GeneLocus).where(GeneLocus.css_id.in_([r['GeneName'] for r in expected]))).scalar_one()==0


def test_raw_sample_value_and_zero(client,imported):
    response=client.get('/api/v1/genes/CSS0000001/expression',params={**CTX,'sampleID':'101'})
    expected=[r for r in imported['rows']['expression'] if r['geneID']=='1' and r['sampleID']=='101']
    assert response.status_code==200
    assert [r['raw_fields'] for r in response.json()['items']]==expected
    assert expected[0]['val']=='0'
    assert client.get('/api/v1/genes/CSS0000001/expression',params={**CTX,'sampleID':'151'}).json()['total']==0


def test_provenance_resolves_original_record(client,imported):
    data=client.get('/api/v1/genes/CSS0000001',params=CTX).json();p=data['provenance']
    record=client.get(p['record_url'])
    assert record.status_code==200 and record.json()['raw_fields']==data['raw_fields']
    assert len(p['file_sha256'])==64 and p['import_batch_id']>0
    import hashlib
    assert p['file_sha256']==hashlib.sha256((imported['raw']/p['filename']).read_bytes()).hexdigest()
    assert imported['rows']['catalog'][p['record_no']-1]==record.json()['raw_fields']


def test_profile_has_separate_evidence_and_no_sequences(client):
    response=client.get('/api/v1/genes/CSS0008782/profile',params={**CTX,'limit':2})
    assert response.status_code==200
    data=response.json();assert data['experimental_validation_status']=='not_provided'
    assert data['sequence_records']>0 and 'sequence' not in data['evidence']
    assert data['evidence']['domain']['total']>len(data['evidence']['domain']['items'])
    assert all(r['evidence_class']=='existing_domain_annotation' for r in data['evidence']['domain']['items'])


def test_sequence_on_demand(client,imported):
    response=client.get('/api/v1/genes/CSS0000001/sequences/CDS',params=CTX)
    assert response.status_code==200
    expected=next(r for r in imported['rows']['sequence'] if r['chrom_tag_id']=='1')
    actual=response.json()['items'][0]['raw_fields']
    assert actual['CDS']==expected['CDS'] and 'Protein' not in actual


def test_original_ordered_network(client,imported):
    row=imported['rows']['network'][0];gid=row['tag1']
    response=client.get(f'/api/v1/genes/{gid}/networks',params={**CTX,'dataset_code':'ectropis','limit':200})
    expected=[r for r in imported['rows']['network'] if r['tag1']==gid]
    assert [r['raw_fields'] for r in response.json()['items']]==expected


@pytest.mark.parametrize('path,params',[
    ('/api/v1/genes/1',{}),('/api/v1/genes/CSS0000001.1',{}),
    ('/api/v1/genes/CSS0000001/blast',{'limit':0}),('/api/v1/genes/CSS0000001/blast',{'limit':201}),
    ('/api/v1/genes/CSS0000001/blast',{'offset':-1}),('/api/v1/genes/CSS0000001/blast',{'context_key':'bad key'}),
    ('/api/v1/genes/CSS0000001/domains',{'annotation_type':'unknown'}),
    ('/api/v1/genes/CSS0000001/expression',{'sampleID':'101 OR 1=1'}),
    ('/api/v1/genes/CSS0000001/expression',{'sample_id':'101'}),
    ('/api/v1/genes/CSS0000001/sequences/anything',{}),('/api/v1/sources/0/records/1',{}),
    ('/api/v1/id-mappings',{'namespace':'unvalidated','identifier':'anything'}),
])
def test_parameter_validation(client,path,params):
    with_context={**CTX,**params} if path.startswith('/api/v1/genes') or path.startswith('/api/v1/id-mappings') else params
    response=client.get(path,params=with_context)
    assert response.status_code==422 and response.json()['error']['code']=='invalid_parameters'


def test_version_ambiguity(engine,imported,tmp_path,client):
    manifest=copy.deepcopy(imported['manifest']);manifest['context']['key']='second_'+uuid.uuid4().hex
    manifest['files']=[next(r for r in manifest['files'] if r['kind']=='catalog')]
    path=tmp_path/'manifest.json';path.write_text(json.dumps(manifest),encoding='utf-8')
    assert run_import(engine,path,tmp_path/'reports')['status']=='completed'
    response=client.get('/api/v1/genes/CSS0000001')
    assert response.status_code==409 and response.json()['error']['code']=='ambiguous_context'
    assert len(response.json()['error']['details'])>1
    assert client.get('/api/v1/genes/CSS0000001',params=CTX).status_code==200
    partial=client.get('/api/v1/genes/CSS0000001/annotations',params={'context_key':manifest['context']['key']}).json()
    assert partial['source_file_count']==0 and any('尚未导入' in text for text in partial['limitations'])
    answer=client.post('/api/v1/query',json={'question':'查询 CSS0000001 的 GO 注释','context_key':manifest['context']['key']}).json()
    assert '尚未导入' in answer['answer']


@pytest.mark.parametrize('question,status',[
    ('查询 CSS0000001 的基本信息','ok'),('查询 CSS0000173 的 GO 注释','ok'),
    ('查询 CSS0008782 的 PFAM/IPR','ok'),('查询 CSS0037220 的 BLASTP 命中','ok'),
    ('查询 CSS0000001 在 sampleID=101 的原始数值','ok'),
    ('CSS0000001 是否调控抗病？','unsupported'),('SELECT * FROM genes WHERE CSS0000001','unsupported'),
    ('查询 CSS0000001 的表达','needs_clarification'),('查询 CSS0000081.2 的信息','needs_clarification'),
])
def test_controlled_questions(client,question,status):
    response=client.post('/api/v1/query',json={'question':question,**CTX})
    assert response.status_code==200 and response.json()['status']==status
    if status=='ok':
        assert response.json()['result'] is not None
        assert 'provenance' in response.json()['result'] or 'items' in response.json()['result']


def test_query_extra_sql_parameter_rejected(client):
    response=client.post('/api/v1/query',json={'question':'查询 CSS0000001','sql':'DROP TABLE genes'})
    assert response.status_code==422


def test_unavailable_database_returns_safe_error():
    broken=make_engine('postgresql+psycopg://teageneqa@127.0.0.1:1/teageneqa_dev?connect_timeout=1')
    with TestClient(create_app(broken)) as client:
        assert client.get('/api/v1/health').status_code==503
        response=client.get('/api/v1/genes/CSS0000001')
        assert response.status_code==503
        assert 'postgresql://' not in response.text and 'password' not in response.text
