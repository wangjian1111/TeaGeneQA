"""配置切换必须可复现，缺少 Docker 凭据时不能静默连接原生库。"""
import pytest
from sqlalchemy.engine import make_url

from teageneqa import db


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    path = tmp_path / '.env'
    monkeypatch.setattr(db, 'ENV_FILE', path)
    for key in ('DATABASE_URL','TEAGENEQA_DATABASE_MODE','POSTGRES_PASSWORD','TEAGENEQA_DB_PORT'):
        monkeypatch.delenv(key, raising=False)
    return path


def test_project_docker_config_and_special_password(env_file):
    env_file.write_text('# 配置\nPOSTGRES_PASSWORD="test@pw:/#%"\nTEAGENEQA_DB_PORT=55433\n')
    url = make_url(db.database_url())
    assert (url.host,url.port,url.database,url.username,url.password)==('127.0.0.1',55433,'teageneqa_dev','teageneqa','test@pw:/#%')


def test_environment_url_takes_priority_even_with_invalid_file(env_file,monkeypatch):
    env_file.write_text('invalid line')
    expected = 'postgresql+psycopg://test@db:5432/container_test'
    monkeypatch.setenv('DATABASE_URL',expected)
    assert db.database_url()==expected


def test_explicit_native_mode(env_file,monkeypatch):
    monkeypatch.setenv('TEAGENEQA_DATABASE_MODE','native')
    assert db.database_url()==db.NATIVE_DATABASE_URL


def test_missing_password_fails_without_native_fallback(env_file):
    with pytest.raises(ValueError,match='POSTGRES_PASSWORD'):
        db.database_url()


def test_process_values_override_project_values(env_file,monkeypatch):
    env_file.write_text('POSTGRES_PASSWORD=file_value\nTEAGENEQA_DB_PORT=55433\n')
    monkeypatch.setenv('POSTGRES_PASSWORD','process_value')
    monkeypatch.setenv('TEAGENEQA_DB_PORT','55434')
    url = make_url(db.database_url())
    assert (url.password,url.port)==('process_value',55434)


@pytest.mark.parametrize('content', ['bad line','POSTGRES_PASSWORD="unclosed','A=1\nA=2'])
def test_invalid_env_does_not_echo_contents(env_file,content):
    env_file.write_text(content)
    with pytest.raises(ValueError) as exc:
        db.read_project_env()
    assert content not in str(exc.value)


@pytest.mark.parametrize('port',['0','65536','not_a_number'])
def test_invalid_port(env_file,port):
    env_file.write_text(f'POSTGRES_PASSWORD=test\nTEAGENEQA_DB_PORT={port}\n')
    with pytest.raises(ValueError):
        db.database_url()


def test_invalid_mode(env_file):
    env_file.write_text('TEAGENEQA_DATABASE_MODE=unknown\n')
    with pytest.raises(ValueError,match='MODE'):
        db.database_url()


def test_empty_explicit_url_is_error(env_file,monkeypatch):
    monkeypatch.setenv('DATABASE_URL','')
    with pytest.raises(ValueError,match='DATABASE_URL'):
        db.database_url()
