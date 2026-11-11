"""明确的响应契约：原始字段与来源证据一起返回。"""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Provenance(BaseModel):
    source_file_id: int
    filename: str
    file_sha256: str
    import_batch_id: int
    record_no: int
    source_row_id: str
    context_key: str
    record_url: str


class GeneView(BaseModel):
    gene_id: str
    context_key: str
    context_label: str
    reference_metadata: dict[str,Any]
    semantic_status: str
    raw_fields: dict[str,str]
    provenance: Provenance


class EvidenceItem(BaseModel):
    raw_fields: dict[str,str]
    evidence_class: str
    lexical_status: str
    semantic_status: str
    provenance: Provenance


class EvidencePage(BaseModel):
    gene_id: str
    context_key: str
    total: int
    limit: int
    offset: int
    items: list[EvidenceItem]
    source_file_count: int
    limitations: list[str] = Field(default_factory=list)


class ProfileView(BaseModel):
    gene: GeneView
    evidence: dict[str,EvidencePage]
    sequence_records: int
    experimental_validation_status: Literal['not_provided'] = 'not_provided'
    limitations: list[str]


class QueryRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    question: str = Field(min_length=1,max_length=500)
    context_key: str | None = Field(None,pattern=r'^[A-Za-z0-9_.-]{1,128}$')
    limit: int = Field(20,ge=1,le=200)
    offset: int = Field(0,ge=0,le=1_000_000)


class QueryResponse(BaseModel):
    status: Literal['ok','unsupported','needs_clarification']
    mode: Literal['rules_and_templates'] = 'rules_and_templates'
    intent: str | None = None
    answer: str
    result: dict[str,Any] | None = None
    limitations: list[str] = Field(default_factory=list)


class HealthView(BaseModel):
    status: Literal['ok','degraded']
    service_version: str
    database: str
    migration: str | None
