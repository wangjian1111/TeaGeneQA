#!/usr/bin/env python3
"""将本项目原生库只读备份并恢复到空 Docker 库；绝不清空或覆盖目标。"""
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from teageneqa.db import read_project_env  # noqa: E402


def fingerprint_database(conn):
    """按主键排序后流式散列所有行/字段；不把序列或七百万记录载入内存。"""
    tables = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='teageneqa' ORDER BY tablename").fetchall()
    result = {'tables': {}, 'sequences': {}}
    for (table,) in tables:
        keys = conn.execute("""SELECT a.attname FROM pg_index i
            JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=ANY(i.indkey)
            WHERE i.indrelid=%s::regclass AND i.indisprimary ORDER BY array_position(i.indkey,a.attnum)""",
            ('teageneqa.' + table,)).fetchall()
        if not keys:
            raise RuntimeError('存在没有主键的表，无法按稳定顺序校验。')
        identifier = sql.Identifier('teageneqa', table)
        count = conn.execute(sql.SQL('SELECT count(*) FROM {}').format(identifier)).fetchone()[0]
        statement = sql.SQL('COPY (SELECT * FROM {} ORDER BY {}) TO STDOUT WITH (FORMAT binary)').format(
            identifier, sql.SQL(',').join(sql.Identifier(key) for (key,) in keys))
        digest = hashlib.sha256()
        with conn.cursor().copy(statement) as copy:
            for chunk in copy:
                digest.update(chunk)
        result['tables'][table] = {'rows': count, 'all_fields_sha256': digest.hexdigest()}
        print(f'已核对 {table}: {count:,} 行', flush=True)
    for (sequence,) in conn.execute("SELECT sequencename FROM pg_sequences WHERE schemaname='teageneqa' ORDER BY sequencename"):
        result['sequences'][sequence] = list(conn.execute(sql.SQL('SELECT last_value,is_called FROM {}').format(sql.Identifier('teageneqa', sequence))).fetchone())
    result['indexes'] = [list(row) for row in conn.execute("SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='teageneqa' ORDER BY indexname")]
    result['constraints'] = [list(row) for row in conn.execute("""SELECT c.conrelid::regclass::text,c.conname,pg_get_constraintdef(c.oid),c.convalidated
        FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace
        WHERE n.nspname='teageneqa' ORDER BY 1,2""")]
    result['revision'] = conn.execute('SELECT version_num FROM teageneqa.alembic_version').fetchone()[0]
    return result


def main():
    local = read_project_env()
    port = int(local.get('TEAGENEQA_DB_PORT', '55433'))
    if port == 55432 or not local.get('POSTGRES_PASSWORD'):
        raise SystemExit('目标必须为独立的 Docker 端口，且 .env 必须配置密码。')
    folder = ROOT / '.runtime' / ('docker_migration_' + datetime.now().astimezone().strftime('%Y%m%d_%H%M%S'))
    folder.mkdir(mode=0o700, parents=True, exist_ok=False)
    archive = folder / 'teageneqa.dump'
    report = {'started_at': datetime.now().astimezone().isoformat(), 'status': 'started',
              'source': {'host':'127.0.0.1','port':55432,'database':'teageneqa_dev'},
              'target': {'host':'127.0.0.1','port':port,'database':'teageneqa_dev'}, 'archive':str(archive)}
    report_path = folder / 'migration.json'
    # 子进程只使用明确的本项目地址；密码不放入命令行或日志。
    child_env = os.environ.copy()
    for key in list(child_env):
        if key.startswith('PG'):
            child_env.pop(key)
    child_env['PGCONNECT_TIMEOUT'] = '10'
    try:
        with psycopg.connect(host='127.0.0.1',port=55432,user='teageneqa',dbname='teageneqa_dev',connect_timeout=10) as source, \
             psycopg.connect(host='127.0.0.1',port=port,user='teageneqa',dbname='teageneqa_dev',password=local['POSTGRES_PASSWORD'],connect_timeout=10,autocommit=True) as target:
            source.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            objects = target.execute("""SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_%'""").fetchone()[0]
            schemas = target.execute("SELECT nspname FROM pg_namespace WHERE nspname NOT IN ('public','pg_catalog','information_schema') AND nspname NOT LIKE 'pg_%'").fetchall()
            if objects or schemas:
                raise RuntimeError('Docker 目标不是空库；已拒绝恢复，现有数据保持不变。')
            for label, conn in [('source',source),('target',target)]:
                report[label]['server_version'] = conn.execute('SHOW server_version').fetchone()[0]
                if conn.info.server_version // 10000 != 17:
                    raise RuntimeError('本迁移脚本仅验证过 PostgreSQL 17，请先检查版本。')
            snapshot = source.execute('SELECT pg_export_snapshot()').fetchone()[0]
            print('目标空库检查通过，开始只读备份原生库。', flush=True)
            with (folder / 'pg_dump.log').open('w') as log:
                subprocess.run([str(ROOT/'.runtime/postgres/bin/pg_dump'),'-h','127.0.0.1','-p','55432','-U','teageneqa','-d','teageneqa_dev',
                                '--format=custom','--schema=teageneqa','--no-owner','--no-acl','--snapshot='+snapshot,'--file='+str(archive)],
                               env=child_env,stdout=log,stderr=subprocess.STDOUT,check=True)
            archive.chmod(0o600)
            with archive.open('rb') as handle:
                report['archive_sha256'] = hashlib.file_digest(handle,'sha256').hexdigest()
            report['archive_bytes'] = archive.stat().st_size
            report['source_fingerprint'] = fingerprint_database(source)
            # 再次检查，防止备份期间有人向目标写入后被无意混合。
            if target.execute("SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname='teageneqa')").fetchone()[0]:
                raise RuntimeError('目标结构在备份期间发生变化，已停止。')
            child_env['PGPASSWORD'] = local['POSTGRES_PASSWORD']
            print('备份完成，开始单事务恢复到 Docker。', flush=True)
            with (folder / 'pg_restore.log').open('w') as log:
                subprocess.run([str(ROOT/'.runtime/postgres/bin/pg_restore'),'-h','127.0.0.1','-p',str(port),'-U','teageneqa','-d','teageneqa_dev',
                                '--single-transaction','--exit-on-error','--no-owner','--no-acl',str(archive)],
                               env=child_env,stdout=log,stderr=subprocess.STDOUT,check=True)
            child_env.pop('PGPASSWORD',None)
            report['target_fingerprint'] = fingerprint_database(target)
            report['checks'] = {key:report['source_fingerprint'][key]==report['target_fingerprint'][key]
                                for key in ('tables','sequences','indexes','constraints','revision')}
            report['status'] = 'passed' if all(report['checks'].values()) else 'failed'
    except Exception as exc:
        # 错误日志只记录异常类型，避免库连接异常把凭据或原始记录带到终端。
        report['status'] = 'failed'
        report['error_type'] = type(exc).__name__
        if type(exc) is RuntimeError:
            report['reason'] = str(exc)
            print(str(exc), flush=True)
        print('迁移失败，已保留原生库和本地备份；检查迁移目录中的日志。', flush=True)
    finally:
        report['finished_at'] = datetime.now().astimezone().isoformat()
        report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        report_path.chmod(0o600)
        print(json.dumps({'status':report['status'],'report':str(report_path)},ensure_ascii=False),flush=True)
    raise SystemExit(0 if report['status']=='passed' else 1)


if __name__ == '__main__':
    main()
