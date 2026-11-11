"""测试只创建随机独立库；真实开发库不参与删除、清空或回滚操作。"""
import csv
import json
import os
import uuid
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy.engine import make_url

from teageneqa.api import create_app
from teageneqa.db import database_url, make_engine
from teageneqa.importer import run_import

ROOT=Path(__file__).resolve().parents[1]


def read_csv(path):
    with path.open(encoding='utf-8-sig',newline='') as handle:return list(csv.DictReader(handle))


def write_csv(path,rows,headers=None,encoding='utf-8'):
    with path.open('w',encoding=encoding,newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=headers or list(rows[0]));writer.writeheader();writer.writerows(rows)


@pytest.fixture(scope='session')
def engine():
    parent=make_url(os.getenv('TEAGENEQA_TEST_ADMIN_URL',database_url()))
    name='teageneqa_test_'+uuid.uuid4().hex
    admin=make_engine(parent.set(database='postgres').render_as_string(hide_password=False))
    with admin.connect().execution_options(isolation_level='AUTOCOMMIT') as conn:
        with conn.connection.driver_connection.cursor() as cursor:cursor.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    url=parent.set(database=name).render_as_string(hide_password=False)
    cfg=Config(str(ROOT/'alembic.ini'));cfg.attributes['database_url']=url
    command.upgrade(cfg,'head');test_engine=make_engine(url)
    try:yield test_engine
    finally:
        test_engine.dispose()
        # 名称由本测试生成，不接受开发库名作为待删参数。
        assert name.startswith('teageneqa_test_') and len(name)==len('teageneqa_test_')+32
        with admin.connect().execution_options(isolation_level='AUTOCOMMIT') as conn:
            with conn.connection.driver_connection.cursor() as cursor:cursor.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
        admin.dispose()


@pytest.fixture(scope='session')
def real_fixture(tmp_path_factory):
    folder=tmp_path_factory.mktemp('real_csv_fixture');raw=folder/'raw';raw.mkdir()
    source=ROOT/'data/raw';catalog=read_csv(source/'tpia_chrom_tag_css.csv')
    wanted={'1','10','81','173','37220','14664','8782','19674','5168','27431'}
    wanted.update(r['ID'] for r in catalog if not r['GeneName'].startswith('CSS'))
    names={r['GeneName']:r['ID'] for r in catalog}
    mappings=[]
    for row in read_csv(source/'tpia_gene_id_convert_css.csv'):
        base=row['SCZV2'].rsplit('.',1)[0]
        if names[base] in wanted or row['Biyun']=='GWHTAMJR007835':
            mappings.append(row);wanted.add(names[base])
    with (source/'tpia_ectropis_co_css.csv').open(newline='') as handle:
        reader=csv.DictReader(handle);network=[next(reader) for _ in range(3)]
    for row in network:wanted.update([names[row['tag1']],names[row['tag2']]])
    originals={'catalog':[r for r in catalog if r['ID'] in wanted], 'mapping':mappings, 'network':network}
    filemap={'catalog':'tpia_chrom_tag_css.csv','mapping':'tpia_gene_id_convert_css.csv','network':'tpia_ectropis_co_css.csv',
             'annotation':'tpia_gene_annotation_css.csv','blast':'tpia_annoblastpdetail_css.csv','domain':'tpia_annopfamdetail_css.csv',
             'factor':'tpia_tfactor_css.csv','expression':'tpia_gene_expro_css.csv','sequence':'tpia_chrom_sequence_css.csv'}
    for kind,filename in filemap.items():
        if kind in originals:continue
        ref={'annotation':'GeneID','factor':'tag_id','sequence':'chrom_tag_id'}.get(kind,'geneID')
        rows=[]
        with (source/filename).open(newline='') as handle:
            for row in csv.DictReader(handle):
                if row[ref] in wanted:
                    if kind=='expression' and row['sampleID'] not in {'101','102','150','151','234'}:continue
                    rows.append(row)
        originals[kind]=rows
    entries=[]
    for kind,filename in filemap.items():
        write_csv(raw/filename,originals[kind])
        entries.append({'path':filename,'kind':kind,'dataset_code':'ectropis' if kind=='network' else ''})
    manifest={'format_version':1,'context':{'key':'real_fixture','label':'真实 CSV 选取的测试夹具（不是完整导出）','metadata':{'source_genome_code_raw':'3','assembly_version':None,'mapping_anchor_namespace':'SCZV2'}},'raw_dir':str(raw),'files':entries}
    path=folder/'manifest.json';path.write_text(json.dumps(manifest,ensure_ascii=False),encoding='utf-8')
    return {'manifest':manifest,'path':path,'raw':raw,'rows':originals,'folder':folder,'files':filemap}


@pytest.fixture(scope='session')
def imported(engine,real_fixture):
    report=run_import(engine,real_fixture['path'],real_fixture['folder']/'reports',chunk_size=2)
    assert report['status']=='completed',report
    return real_fixture


@pytest.fixture
def client(engine,imported):
    with TestClient(create_app(engine)) as result:yield result
