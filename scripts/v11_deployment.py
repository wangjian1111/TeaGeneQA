#!/usr/bin/env python3
"""独立 Compose 验证：新项目/端口/卷，只停止自己的容器，保留所有数据卷。"""
import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import psycopg
import requests
from psycopg.rows import dict_row

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.v11_oracle import TABLES
from teageneqa.db import read_project_env

RUNTIME=ROOT/'reports/v1_1_runtime'
STATE=RUNTIME/'deployment_state.json'
REPORT=RUNTIME/'deployment_validation.json'
DOCKER=str(Path.home()/'.docker/bin/docker')
PORTS={'db':55434,'api':18110,'web':18511}


def now():return datetime.now().astimezone().isoformat()


def save(path,data):path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')


def service_health(port,path):
    try:
        r=requests.get(f'http://127.0.0.1:{port}{path}',timeout=4)
        return {'status_code':r.status_code,'body':r.text[:300]}
    except requests.RequestException:return {'status_code':None}


def compose(state,*args,capture=False):
    assert state['project'].startswith('teageneqa-quality-') and state['ports']==PORTS
    env=os.environ.copy();env['PATH']=str(Path(DOCKER).parent)+':/Applications/Docker.app/Contents/Resources/bin:'+env.get('PATH','')
    # 防止调用终端残留生产端口/密码，覆盖隔离 --env-file 中的值。
    for key in ['POSTGRES_PASSWORD','TEAGENEQA_DB_PORT','TEAGENEQA_API_PORT','TEAGENEQA_WEB_PORT','COMPOSE_PROJECT_NAME','COMPOSE_FILE']:
        env.pop(key,None)
    cmd=[DOCKER,'--context','desktop-linux','compose','--project-directory',str(ROOT),'-p',state['project'],
         '--env-file',state['env_file'],'-f',str(ROOT/'compose.yaml'),'-f',str(ROOT/'compose.quality.yaml'),*args]
    if capture:return subprocess.check_output(cmd,env=env,text=True)
    with (RUNTIME/('compose_'+args[0]+'.log')).open('a') as handle:
        subprocess.run(cmd,env=env,stdout=handle,stderr=subprocess.STDOUT,check=True)


def snapshot(port,password):
    with psycopg.connect(host='127.0.0.1',port=port,user='teageneqa',dbname='teageneqa_dev',password=password,connect_timeout=5,row_factory=dict_row) as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        sources=conn.execute('SELECT id,filename,kind,sha256,row_count FROM teageneqa.source_file ORDER BY id').fetchall()
        for source in sources:
            # 表名来自固定白名单，不来自外部输入。
            source['actual_rows']=conn.execute('SELECT count(*) AS n FROM teageneqa.'+TABLES[source['kind']]+' WHERE source_file_id=%s',(source['id'],)).fetchone()['n']
        return {'sources':sources,'standard_css_count':conn.execute('SELECT count(*) AS n FROM teageneqa.gene_locus').fetchone()['n'],
            'entity_count':conn.execute('SELECT count(*) AS n FROM teageneqa.entity_record').fetchone()['n'],
            'migration':conn.execute('SELECT version_num FROM teageneqa.alembic_version').fetchone()['version_num']}


def current_services():
    return {'api':service_health(18000,'/api/v1/health'),'web':service_health(18501,'/_stcore/health')}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['start','resume','persistence','stop'])
    action=parser.parse_args().action;RUNTIME.mkdir(parents=True,exist_ok=True)
    report=json.loads(REPORT.read_text()) if REPORT.exists() else {'started_at':now(),'checks':{}}
    try:
        if action=='start':
            if STATE.exists():raise RuntimeError('已有隔离环境状态，拒绝创建重叠测试环境。')
            for port in PORTS.values():
                with socket.socket() as sock:
                    sock.bind(('127.0.0.1',port))
            project='teageneqa-quality-'+datetime.now().strftime('%Y%m%d%H%M%S')
            folder=ROOT/'.runtime'/project;folder.mkdir(mode=0o700,parents=True,exist_ok=False)
            env_file=folder/'compose.env'
            fd=os.open(env_file,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as handle:
                handle.write('POSTGRES_PASSWORD='+secrets.token_urlsafe(32)+'\nTEAGENEQA_DB_PORT=55434\nTEAGENEQA_API_PORT=18110\nTEAGENEQA_WEB_PORT=18511\n')
            state={'project':project,'env_file':str(env_file),'ports':PORTS,'api_url':'http://127.0.0.1:18110','created_at':now()}
            save(STATE,state)
            report['original_services_before']=current_services()
            report['original_database_before']=snapshot(55433,read_project_env()['POSTGRES_PASSWORD'])
            config=json.loads(compose(state,'config','--format','json',capture=True))
            actual_ports={int(p['published']) for svc in config['services'].values() for p in svc.get('ports',[])}
            assert actual_ports==set(PORTS.values())
            assert all(v['name'].startswith(project+'_') and not v.get('external') for v in config['volumes'].values())
            assert all(p.get('host_ip')=='127.0.0.1' for svc in config['services'].values() for p in svc.get('ports',[]))
            assert any(v.get('read_only') and v['target']=='/app/data/raw' for v in config['services']['importer']['volumes'])
            report['checks']['isolated_configuration']=True
            report['isolation']={'project':project,'ports':PORTS,'volumes':{k:v['name'] for k,v in config['volumes'].items()},
                                 'resource_limits':{k:{f:v.get(f) for f in ['cpus','mem_limit']} for k,v in config['services'].items()}}
            print('隔离配置检查通过，启动全套 Compose 并只读挂载真实 CSV。',flush=True)
            compose(state,'up','-d','--no-build','--wait','--wait-timeout','900','api','web')
            ps_text=compose(state,'ps','-a','--format','json',capture=True).strip()
            report['compose_ps']=json.loads(ps_text) if ps_text.startswith('[') else [json.loads(line) for line in ps_text.splitlines()]
            # ps 的 Command 可能包含连接参数，报告只保存白名单状态字段。
            report['compose_ps']=[{k:v for k,v in row.items() if k in ['Name','Service','State','Health','ExitCode','Image']} for row in report['compose_ps']]
            report['checks']['full_compose_started']=True
        else:
            state=json.loads(STATE.read_text())
            if action=='resume':
                compose(state,'up','-d','--no-build','--wait','--wait-timeout','900','api','web')
        password=read_project_env(Path(state['env_file']))['POSTGRES_PASSWORD']
        if action=='persistence':
            before=snapshot(PORTS['db'],password)
            ids_before=compose(state,'ps','-q','db',capture=True).strip()
            compose(state,'stop','db')
            failed=service_health(PORTS['api'],'/api/v1/health')
            assert failed['status_code']==503,failed
            compose(state,'up','-d','--no-deps','--force-recreate','--wait','--wait-timeout','120','db')
            ids_after=compose(state,'ps','-q','db',capture=True).strip()
            after=snapshot(PORTS['db'],password)
            assert ids_before!=ids_after and before==after
            report['persistence']={'container_before':ids_before,'container_after':ids_after,'data_snapshot_before':before,
                                   'data_snapshot_after':after,'unavailable_database_health':failed}
            report['checks']['container_recreated_data_preserved']=True
        if action!='stop':
            health=service_health(PORTS['api'],'/api/v1/health')
            web=service_health(PORTS['web'],'/_stcore/health')
            assert health['status_code']==web['status_code']==200
            report['isolated_health']={'api':health,'web':web}
            data=snapshot(PORTS['db'],password)
            assert all(r['row_count']==r['actual_rows'] for r in data['sources'])
            assert data==report['original_database_before']
            report['isolated_data']=data;report['checks']['full_real_data_counts_and_hashes']=True
        else:
            compose(state,'stop')
            report['isolated_services_stopped_at']=now();report['checks']['test_volumes_retained']=True
        report['original_database_after']=snapshot(55433,read_project_env()['POSTGRES_PASSWORD'])
        assert report['original_database_after']==report['original_database_before']
        report['original_services_after']=current_services()
        assert all(v['status_code']==200 for v in report['original_services_after'].values())
        report['checks']['original_database_unchanged']=True
        report['checks']['original_services_healthy']=True
        report['status']='passed';report['updated_at']=now()
    except Exception as exc:
        report['status']='failed';report['error_type']=type(exc).__name__
        # 原始异常/命令不写终端，避免连接字符串泄露；阶段日志留在本机供检查。
        save(REPORT,report)
        raise SystemExit('隔离部署验证未完成，请检查本地部署报告和阶段日志。') from None
    save(REPORT,report)
    print(json.dumps({'status':report['status'],'action':action,'project':state['project'],'checks':report['checks']},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
