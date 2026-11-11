#!/usr/bin/env python3
"""在已登记的隔离 PostgreSQL 上执行测试；凭据只在子进程环境中传递。"""
import json
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import URL

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from teageneqa.db import read_project_env


def main():
    runtime=ROOT/'reports/v1_1_runtime'
    state=json.loads((runtime/'deployment_state.json').read_text())
    assert state['project'].startswith('teageneqa-quality-') and state['ports']['db']==55434
    secret=read_project_env(Path(state['env_file']))['POSTGRES_PASSWORD']
    url=URL.create('postgresql+psycopg',username='teageneqa',password=secret,host='127.0.0.1',port=55434,database='teageneqa_dev').render_as_string(hide_password=False)
    env=os.environ.copy();env.update(DATABASE_URL=url,TEAGENEQA_TEST_ADMIN_URL=url,TEAGENEQA_FULL_DATA='1',
        TEAGENEQA_VERIFICATION_OUTPUT=str(runtime/'full_csv_verification.json'))
    log=runtime/'pytest.log'
    with log.open('w') as handle:
        completed=subprocess.run([sys.executable,'-m','pytest','-q','--junitxml='+str(runtime/'pytest.xml')],cwd=ROOT,env=env,stdout=handle,stderr=subprocess.STDOUT)
    content=log.read_text()
    if secret in content:log.write_text(content.replace(secret,'[REDACTED]'))
    print(json.dumps({'pytest_return_code':completed.returncode,'test_server_port':55434,'junit':str(runtime/'pytest.xml')},ensure_ascii=False))
    raise SystemExit(completed.returncode)


if __name__=='__main__':main()
