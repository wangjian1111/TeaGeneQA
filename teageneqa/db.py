"""数据库入口：运行服务不自动建表，结构变更统一由 Alembic 管理。"""
import os
import re
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, URL

NATIVE_DATABASE_URL = "postgresql+psycopg://teageneqa@127.0.0.1:55432/teageneqa_dev"
SCHEMA = "teageneqa"
ENV_FILE = Path(__file__).resolve().parents[1] / '.env'


def read_project_env(path: Path | None = None) -> dict[str, str]:
    """读取项目生成的 KEY=value 配置；不执行 shell，也不展开变量。

    支持空行、整行注释、成对的单/双引号；行内注释和变量插值不属于此格式。
    不写入 os.environ，避免覆盖部署环境或污染测试。
    """
    path = path if path is not None else ENV_FILE
    if not path.exists():
        return {}
    result = {}
    for line_no, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, separator, value = line.partition('=')
        key, value = key.strip(), value.strip()
        if not separator or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key):
            raise ValueError(f'.env 第 {line_no} 行格式无效；配置内容未输出。')
        if value.startswith(('"', "'")):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f'.env 第 {line_no} 行引号未闭合。')
            value = value[1:-1]
        if key in result:
            raise ValueError(f'.env 第 {line_no} 行存在重复配置项。')
        result[key] = value
    return result


def database_url() -> str:
    # 显式连接串优先，兼容 Compose 容器内的 db:5432 和独立测试服务器。
    if 'DATABASE_URL' in os.environ:
        if not os.environ['DATABASE_URL'].strip():
            raise ValueError('DATABASE_URL 不能为空。')
        return os.environ['DATABASE_URL']
    local = read_project_env()
    if local.get('DATABASE_URL'):
        return local['DATABASE_URL']
    mode = os.getenv('TEAGENEQA_DATABASE_MODE', local.get('TEAGENEQA_DATABASE_MODE', 'docker'))
    if mode == 'native':
        return NATIVE_DATABASE_URL
    if mode != 'docker':
        raise ValueError('TEAGENEQA_DATABASE_MODE 只支持 docker 或 native。')
    password = os.getenv('POSTGRES_PASSWORD', local.get('POSTGRES_PASSWORD', ''))
    if not password:
        raise ValueError('Docker 数据库缺少 POSTGRES_PASSWORD；请检查项目 .env。')
    port = int(os.getenv('TEAGENEQA_DB_PORT', local.get('TEAGENEQA_DB_PORT', '55433')))
    if not 1 <= port <= 65535:
        raise ValueError('TEAGENEQA_DB_PORT 必须在 1..65535 内。')
    # URL.create 正确编码特殊字符，密码只在内存传递，不输出连接串。
    return URL.create('postgresql+psycopg', username='teageneqa', password=password,
                      host='127.0.0.1', port=port, database='teageneqa_dev').render_as_string(hide_password=False)


def make_engine(url: str | None = None) -> Engine:
    # 所有业务表均显式带 Schema。固定默认 search_path，避免同名角色/Schema
    # 使 Alembic 把 teageneqa 识别为默认 Schema，从而误判为缺少全部表。
    return create_engine(url or database_url(), pool_pre_ping=True, hide_parameters=True,
                         connect_args={"options":"-csearch_path=public"})
