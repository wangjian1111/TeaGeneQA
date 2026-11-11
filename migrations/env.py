"""迁移严格限定 teageneqa Schema；不管理其他 Schema。"""
from alembic import context
from sqlalchemy import text

from teageneqa.db import SCHEMA, make_engine
from teageneqa.models import Base

config=context.config
target_metadata=Base.metadata


def include_name(name,type_,parent_names):
    return name==SCHEMA if type_=='schema' else True


def run_online():
    engine=make_engine(config.attributes.get('database_url'))
    with engine.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS {SCHEMA}'))
        connection.commit()
        context.configure(connection=connection,target_metadata=target_metadata,
                          include_schemas=True,include_name=include_name,
                          version_table_schema=SCHEMA,compare_type=True)
        with context.begin_transaction():context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    raise RuntimeError('本项目迁移要求连接明确的开发数据库，不提供未知目标的离线迁移')
else:run_online()
