import json
import copy
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from teageneqa.api import create_app
from teageneqa.db import make_engine
from teageneqa.importer import run_import
from teageneqa.tools import TOOLS, invoke_tool, tool_catalog

CTX={'context_key':'real_fixture'}


@pytest.mark.parametrize('name,gene,kind',[
    ('get_gene_annotations','CSS0000173','annotation'),('get_blast_hits','CSS0037220','blast'),
    ('get_protein_domains','CSS0008782','domain'),('get_expression_values','CSS0000001','expression'),
    ('get_transcription_factors','CSS0000001','factor'),
])
def test_tool_real_csv_fields_and_provenance(client,imported,name,gene,kind):
    params={**CTX,'gene_id':gene,'limit':50}
    if kind=='expression':params['sample_id']='101'
    response=client.post('/api/v1/tools/'+name,json=params)
    assert response.status_code==200,response.text
    result=response.json();data=result['data']
    # 预期关联必须取自原始目录，不能假定 CSS 数字后缀就是内部 ID。
    identifier=next(r['ID'] for r in imported['rows']['catalog'] if r['GeneName']==gene)
    field={'annotation':'GeneID','factor':'tag_id'}.get(kind,'geneID')
    expected=[r for r in imported['rows'][kind] if r[field]==identifier and (kind!='expression' or r['sampleID']=='101')]
    assert data['total']==len(expected)
    assert [r['raw_fields'] for r in data['items']]==expected
    assert all(r['provenance']['filename']==imported['files'][kind] and r['provenance']['record_no']>0 for r in data['items'])
    assert result['evidence_policy']['experimental_validation']['status']=='not_provided'


def test_tool_mapping_keeps_all_candidates(client,imported):
    expected=[r for r in imported['rows']['mapping'] if r['Biyun']=='GWHTAMJR007835']
    collected=[]
    for offset in range(0,len(expected),50):
        response=client.post('/api/v1/tools/resolve_gene_identifier',json={**CTX,'namespace':'Biyun','identifier':'GWHTAMJR007835','target_namespace':'SCZV2','limit':50,'offset':offset})
        data=response.json();assert data['status']=='ambiguous'
        assert data['data']['total']==len(expected)>1
        collected.extend(r['raw_fields'] for r in data['data']['items'])
    assert collected==expected


def test_tool_profile_does_not_load_sequences(client):
    data=client.post('/api/v1/tools/get_gene_profile',json={**CTX,'gene_id':'CSS0008782','limit':2}).json()['data']
    assert data['experimental_validation_status']=='not_provided'
    assert data['sequence_records']>0
    assert 'Protein' not in json.dumps(data)


def test_source_tool_explicitly_omits_sequences(client):
    page=client.get('/api/v1/genes/CSS0000001/sequences/CDS',params=CTX).json()
    origin=page['items'][0]['provenance']
    response=client.post('/api/v1/tools/get_source_evidence',json={'source_file_id':origin['source_file_id'],'record_no':origin['record_no']})
    data=response.json()['data'];assert response.status_code==200
    assert {r['field'] for r in data['omitted_fields']}=={'Protein','Transcript','Gene','Upstream','Downstream','CDS'}
    assert 'CDS' not in data['raw_fields']
    assert data['provenance']==origin
    assert data['source_kind']=='sequence' and data['evidence_class']=='raw_sequence'


def test_source_tool_keeps_homology_separate_from_experiment(client):
    hit=client.get('/api/v1/genes/CSS0037220/blast',params=CTX).json()['items'][0]
    ref=hit['provenance']
    result=client.post('/api/v1/tools/get_source_evidence',json={'source_file_id':ref['source_file_id'],'record_no':ref['record_no']}).json()
    assert result['data']['raw_fields']==hit['raw_fields']
    assert result['data']['source_kind']=='blast'
    assert result['data']['evidence_class']=='existing_sequence_similarity_hit'
    assert result['evidence_policy']['experimental_validation']=={'status':'not_provided','items':[]}


@pytest.mark.parametrize('name,args,status',[
    ('run_sql',{'sql':'SELECT 1'},404),
    ('get_gene_profile',{'gene_id':'CSS0000001','sql':'DROP TABLE x'},422),
    ('get_blast_hits',{'gene_id':'CSS0000001','limit':51},422),
    ('get_blast_hits',{'gene_id':'CSS0000001','limit':True},422),
    ('get_blast_hits',{'gene_id':'CSS0000001','offset':-1},422),
    ('get_gene_profile',{'gene_id':'Caffeine'},422),
    ('get_expression_values',{'gene_id':'CSS0000001'},422),
    ('get_source_evidence',{'source_file_id':0,'record_no':1},422),
    ('get_gene_profile',{'gene_id':'CSS9999999'},404),
])
def test_tool_parameter_and_boundary_errors(client,name,args,status):
    response=client.post('/api/v1/tools/'+name,json={**CTX,**args} if name!='get_source_evidence' else args)
    assert response.status_code==status
    assert response.json()['error']['code']
    assert 'DROP TABLE x' not in response.text


def test_tool_sql_in_identifier_is_data_not_code(client):
    response=client.post('/api/v1/tools/resolve_gene_identifier',json={**CTX,'namespace':'Biyun','identifier':"'; DROP SCHEMA teageneqa CASCADE; --"})
    assert response.status_code==200 and response.json()['status']=='empty'
    assert client.get('/api/v1/health').status_code==200


def test_tools_force_database_read_only(engine,client):
    seen=[]
    def inspect(conn,cursor,statement,parameters,context,executemany):
        if statement.lstrip().startswith('SELECT'):
            with conn.connection.driver_connection.cursor() as cur:
                cur.execute('SHOW transaction_read_only');seen.append(cur.fetchone()[0])
    event.listen(engine,'before_cursor_execute',inspect)
    try:
        response=client.post('/api/v1/tools/get_gene_profile',json={**CTX,'gene_id':'CSS0000001','limit':1})
        assert response.status_code==200 and seen and set(seen)=={'on'}
    finally:event.remove(engine,'before_cursor_execute',inspect)


def test_tool_requires_context_when_versions_are_ambiguous(engine,client,imported,tmp_path):
    manifest=copy.deepcopy(imported['manifest']);manifest['context']['key']='tool_context_'+uuid.uuid4().hex
    path=tmp_path/'manifest.json';path.write_text(json.dumps(manifest),encoding='utf-8')
    assert run_import(engine,path,tmp_path/'reports')['status']=='completed'
    response=client.post('/api/v1/tools/get_gene_profile',json={'gene_id':'CSS0000001'})
    assert response.status_code==409
    assert response.json()['status']=='ambiguous' and response.json()['error']['code']=='ambiguous_context'
    assert client.post('/api/v1/tools/get_gene_profile',json={**CTX,'gene_id':'CSS0000001'}).status_code==200


def test_tools_database_failure_redacts_connection():
    bad=make_engine('postgresql+psycopg://hidden:do_not_echo@127.0.0.1:1/missing?connect_timeout=1')
    try:
        status,body=invoke_tool(bad,'get_gene_profile',{'gene_id':'CSS0000001'})
        assert status==503 and body['status']=='unavailable'
        assert 'do_not_echo' not in json.dumps(body)
    finally:bad.dispose()


def test_tool_schema_catalog(client):
    catalog=client.get('/api/v1/tools').json()
    assert len(catalog['tools'])==len(TOOLS)==8
    for tool in catalog['tools']:
        assert tool['function']['parameters']['additionalProperties'] is False
        assert tool['data_schema']


def test_optional_timing_collects_sql_without_parameters(engine,imported,monkeypatch):
    monkeypatch.setenv('TEAGENEQA_TIMING','1')
    with TestClient(create_app(engine)) as client:
        response=client.get('/api/v1/genes/CSS0000001',params=CTX)
        assert response.status_code==200
        assert 'db;dur=' in response.headers['Server-Timing']
        assert int(response.headers['X-DB-Query-Count'])>=2
        assert 'SELECT' not in response.headers['Server-Timing']
