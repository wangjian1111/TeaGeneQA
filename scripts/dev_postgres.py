#!/usr/bin/env python3
"""只管理本项目 .runtime/pgdata 的本机开发 PostgreSQL。"""
import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / ".runtime/postgres/bin"
DATA = ROOT / ".runtime/pgdata"


def run(name, *args, check=True):
    return subprocess.run([str(BIN/name), *map(str,args)], check=check)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['start','stop','status'])
    args=parser.parse_args()
    if not (BIN/'postgres').exists():
        parser.error('请先按 docs/deployment.md 安装项目内 PostgreSQL 工具')
    if args.action=='status':
        run('pg_ctl','-D',DATA,'status',check=False);return
    if args.action=='stop':
        if DATA.exists():run('pg_ctl','-D',DATA,'stop','-m','fast')
        return
    if not DATA.exists():
        run('initdb','-D',DATA,'-U','teageneqa','--encoding=UTF8','--no-locale','-A','trust')
    elif not (DATA/'PG_VERSION').exists():
        parser.error('目标目录已存在但不是开发集群，拒绝覆盖')
    if run('pg_ctl','-D',DATA,'status',check=False).returncode!=0:
        run('pg_ctl','-D',DATA,'-l',ROOT/'.runtime/postgres.log','-o',f'-h 127.0.0.1 -p 55432 -k {ROOT/".runtime"}', 'start')
    # 仅创建固定项目库；若已存在则复用，不执行删除或重置。
    import psycopg
    with psycopg.connect('host=127.0.0.1 port=55432 user=teageneqa dbname=postgres',autocommit=True) as conn:
        if not conn.execute('SELECT 1 FROM pg_database WHERE datname=%s',('teageneqa_dev',)).fetchone():
            conn.execute('CREATE DATABASE teageneqa_dev')
    print('TeaGeneQA development PostgreSQL: 127.0.0.1:55432 / teageneqa_dev')


if __name__=='__main__':main()
