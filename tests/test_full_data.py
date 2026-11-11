"""使用显式开关核对已导入的真实开发库，始终只读。"""
import json
import os
from pathlib import Path

import pytest

from conftest import ROOT
from teageneqa.db import make_engine
from teageneqa.verification import verify_manifest


@pytest.mark.full_data
@pytest.mark.skipif(os.getenv('TEAGENEQA_FULL_DATA')!='1',reason='设置 TEAGENEQA_FULL_DATA=1 才核对真实开发库')
def test_full_csv_database_counts_and_provenance():
    engine=make_engine()
    try:report=verify_manifest(engine,ROOT/'config/import_manifest.json')
    finally:engine.dispose()
    target=Path(os.getenv('TEAGENEQA_VERIFICATION_OUTPUT',str(ROOT/'reports/v1_runtime/data_verification.json')));target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    assert report['status']=='passed',report['checks']
    assert report['csv_total_rows']==report['database_total_rows']
    assert report['entity_counts']['non_css_unresolved']==13
