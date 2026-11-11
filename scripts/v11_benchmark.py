#!/usr/bin/env python3
"""仅对已登记的隔离 Compose API 做有上限的性能基线；不测生产/现有端口。"""
import argparse
import asyncio
import copy
import csv
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.v11_evaluate import compare_data

RUNTIME=ROOT/'reports/v1_1_runtime'


def percentiles(values):
    if not values:return {'p50':None,'p95':None,'p99':None}
    ordered=sorted(values)
    # nearest-rank；保留原始请求数据，使其他分位数定义也可复算。
    return {f'p{p}':round(ordered[max(0,math.ceil(len(ordered)*p/100)-1)],3) for p in [50,95,99]}


def scenarios(cases):
    result=[]
    for kind in ['basic','annotation','blast','domain','profile']:
        case=next(c for c in cases if c['category']==kind)
        result.append({'name':kind,'kind':kind,'path':case['request']['path'],'params':case['request']['params'],'expected':case['expected_result']})
    case=next(c for c in cases if c['category']=='domain' and c['expected_result']['total']>1)
    expected=copy.deepcopy(case['expected_result']);expected['items']=expected['items'][1:2]
    result.append({'name':'pagination','kind':'domain','path':case['request']['path'],'params':{**case['request']['params'],'limit':1,'offset':1},'expected':expected})
    return result


async def run_case(base,scenario,concurrency,requests_count,rps,trace):
    lock=asyncio.Lock();cursor=[0];records=[];in_flight=[0];peak=[0]
    async with httpx.AsyncClient(base_url=base,timeout=20,limits=httpx.Limits(max_connections=concurrency,max_keepalive_connections=concurrency)) as client:
        for _ in range(5):
            response=await client.get('/api/v1'+scenario['path'],params=scenario['params']);response.raise_for_status()
            assert compare_data(scenario['kind'],response.json(),scenario['expected'])
        started=time.perf_counter()
        async def worker():
            while True:
                async with lock:
                    if cursor[0]>=requests_count:return
                    index=cursor[0];cursor[0]+=1
                    # 同一并发组小批量同时发起，避免低延迟下“配置并发 4，实际始终只有 1”。
                    slot=started+(index//concurrency)*concurrency/rps
                await asyncio.sleep(max(0,slot-time.perf_counter()))
                begin=time.perf_counter();record={'index':index,'scenario':scenario['name'],'concurrency':concurrency}
                in_flight[0]+=1;peak[0]=max(peak[0],in_flight[0])
                try:
                    response=await client.get('/api/v1'+scenario['path'],params=scenario['params'])
                    record['http_ms']=(time.perf_counter()-begin)*1000;record['status_code']=response.status_code
                    timings={part.strip().split(';')[0]:float(part.split('dur=')[1]) for part in response.headers.get('Server-Timing','').split(',') if 'dur=' in part}
                    record.update({'db_execute_ms':timings.get('db'),'server_app_ms':timings.get('app'),
                        'sql_query_count':int(response.headers.get('X-DB-Query-Count','0')),'response_bytes':len(response.content),
                        'semantic_ok':response.status_code==200 and compare_data(scenario['kind'],response.json(),scenario['expected'])})
                except (httpx.HTTPError,ValueError) as exc:
                    record.update({'http_ms':(time.perf_counter()-begin)*1000,'status_code':None,'semantic_ok':False,'error_type':type(exc).__name__})
                finally:in_flight[0]-=1
                records.append(record)
        await asyncio.gather(*(worker() for _ in range(concurrency)))
        elapsed=time.perf_counter()-started
    errors=sum(r.get('status_code')!=200 for r in records);semantic_errors=sum(not r['semantic_ok'] for r in records)
    for record in records:trace.write(json.dumps(record)+'\n')
    result={'scenario':scenario['name'],'request_count':len(records),'concurrency':concurrency,'peak_in_flight':peak[0],'warmup_requests_excluded':5,
        'max_start_rate_rps':rps,'elapsed_seconds':round(elapsed,3),'achieved_rps':round(len(records)/elapsed,3),
        'error_count':errors,'error_rate':errors/len(records),'semantic_error_count':semantic_errors,
        'http_ms':percentiles([r['http_ms'] for r in records]),
        'db_execute_ms':percentiles([r['db_execute_ms'] for r in records if r.get('db_execute_ms') is not None]),
        'server_app_ms':percentiles([r['server_app_ms'] for r in records if r.get('server_app_ms') is not None]),
        'sql_query_counts':sorted({r.get('sql_query_count') for r in records if 'sql_query_count' in r}),
        'timing_header_count':sum(r.get('db_execute_ms') is not None for r in records),'path':scenario['path'],'params':scenario['params']}
    print(json.dumps({k:result[k] for k in ['scenario','concurrency','request_count','error_rate','semantic_error_count','http_ms']},ensure_ascii=False),flush=True)
    return result


async def main_async(args):
    state=json.loads((RUNTIME/'deployment_state.json').read_text());deployment=json.loads((RUNTIME/'deployment_validation.json').read_text())
    u=urlsplit(state['api_url'])
    assert state['project'].startswith('teageneqa-quality-') and deployment['status']=='passed'
    assert u.hostname=='127.0.0.1' and u.port==18110 and state['ports']['db']==55434
    assert deployment['checks']['isolated_configuration'] and deployment['checks']['full_real_data_counts_and_hashes']
    cases=[json.loads(line) for line in (ROOT/'evaluations/v1_1/candidates.jsonl').read_text().splitlines()]
    matrix=scenarios(cases);started=datetime.now().astimezone().isoformat();results=[]
    with (RUNTIME/'performance_requests.jsonl').open('w') as trace:
        for concurrency in [1,4]:
            for scenario in matrix:results.append(await run_case(state['api_url'],scenario,concurrency,args.requests,args.rps,trace))
    docker=str(Path.home()/'.docker/bin/docker')
    from scripts.v11_deployment import compose
    api_id=compose(state,'ps','-q','api',capture=True).strip()
    image=json.loads(subprocess.check_output([docker,'--context','desktop-linux','inspect',api_id,'--format','{{json .Image}}'],text=True))
    code="import sys,json,importlib.metadata as m;print(json.dumps({'python':sys.version.split()[0],**{p:m.version(p) for p in ['fastapi','SQLAlchemy','psycopg','pydantic','streamlit','uvicorn']}}))"
    container_versions=json.loads(subprocess.check_output([docker,'--context','desktop-linux','exec',api_id,'python','-c',code],text=True))
    report={'started_at':started,'finished_at':datetime.now().astimezone().isoformat(),'base_url':state['api_url'],
        'environment':{'client_python':platform.python_version(),'client_os':platform.platform(),'machine':platform.machine(),
            'cpu_count':os.cpu_count(),'isolation':deployment['isolation'],'image_id':image,'container_versions':container_versions},
        'definitions':{'http_ms':'客户端发起请求到完整接收响应体，不含排队限速及 JSON 校验。',
            'db_execute_ms':'同请求成功 cursor.execute 累计耗时，含数据库往返，排除取数/序列化/连接池等待；不是纯 PostgreSQL 计划执行时间。',
            'server_app_ms':'API 中间件处理时间；与 db_execute 不可简单相加。','percentile':'nearest-rank','cache_state':'每组合预热 5 次，未清空 OS/PostgreSQL 缓存。',
            'rate_control':'目标平均启动速率 25 RPS（可下调）；允许至多 concurrency 条请求的微批量突发，记录实际峰值在途数。'},
        'results':results,'total_measured_requests':sum(r['request_count'] for r in results),
        'scope':'单机独立开发环境有限负载基线；未测生产容量、冷启动、大 offset 或多轮统计显著性。'}
    report['status']='passed' if all(r['error_count']==r['semantic_error_count']==0 and r['timing_header_count']==r['request_count'] for r in results) else 'failed'
    (RUNTIME/'performance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    with (RUNTIME/'performance.csv').open('w',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=['scenario','concurrency','requests','http_p50_ms','http_p95_ms','http_p99_ms','db_p50_ms','db_p95_ms','db_p99_ms','error_rate'])
        writer.writeheader()
        for r in results:writer.writerow({'scenario':r['scenario'],'concurrency':r['concurrency'],'requests':r['request_count'],
            **{'http_'+k+'_ms':v for k,v in r['http_ms'].items()},**{'db_'+k+'_ms':v for k,v in r['db_execute_ms'].items()},'error_rate':r['error_rate']})
    return report['status']


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--requests',type=int,default=120);parser.add_argument('--rps',type=int,default=25)
    args=parser.parse_args()
    if not 20<=args.requests<=200 or not 1<=args.rps<=25:parser.error('每组合请求数限 20..200，速率限 1..25 RPS。')
    raise SystemExit(0 if asyncio.run(main_async(args))=='passed' else 1)


if __name__=='__main__':main()
