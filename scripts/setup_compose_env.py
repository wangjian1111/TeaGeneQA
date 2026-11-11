#!/usr/bin/env python3
"""为本地 Compose 排他创建配置，不覆盖已有文件、不打印凭据。"""
import os
import secrets
from pathlib import Path

target=Path(__file__).resolve().parents[1]/'.env'
try:
    descriptor=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
except FileExistsError:
    raise SystemExit('已有 .env，已保留；请手动检查 POSTGRES_PASSWORD。')
with os.fdopen(descriptor,'w',encoding='utf-8') as handle:
    handle.write('POSTGRES_PASSWORD='+secrets.token_urlsafe(32)+'\n')
    handle.write('TEAGENEQA_DATABASE_MODE=docker\n')
    handle.write('TEAGENEQA_API_PORT=18000\nTEAGENEQA_WEB_PORT=18501\nTEAGENEQA_DB_PORT=55433\n')
print('已创建本地 .env，文件权限 0600；凭据未输出。')
