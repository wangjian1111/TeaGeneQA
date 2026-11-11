"""本地快照与词法引用分层；所有来源 ID 均使用 TEXT。"""
from datetime import datetime

from sqlalchemy import (
    BigInteger, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint,
    Identity, Index, Integer, MetaData, Numeric, String, Text, UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

from .db import SCHEMA


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA, naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    })


class ReferenceContext(Base):
    __tablename__ = "reference_context"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    key: Mapped[str] = mapped_column(Text, unique=True)
    label: Mapped[str] = mapped_column(Text)
    metadata_raw: Mapped[dict] = mapped_column(JSONB)
    semantic_status: Mapped[str] = mapped_column(Text, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ImportBatch(Base):
    __tablename__ = "import_batch"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    context_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.reference_context.id"))
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(Text)
    summary: Mapped[dict] = mapped_column(JSONB, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SourceFile(Base):
    __tablename__ = "source_file"
    __table_args__ = (
        UniqueConstraint("context_id", "kind", "dataset_code"),
        UniqueConstraint("id", "context_id"),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    context_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.reference_context.id"))
    batch_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.import_batch.id"))
    kind: Mapped[str] = mapped_column(Text)
    dataset_code: Mapped[str] = mapped_column(Text)
    filename: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    encoding: Mapped[str] = mapped_column(Text)
    delimiter: Mapped[str] = mapped_column(Text)
    headers: Mapped[list] = mapped_column(JSONB)
    row_count: Mapped[int] = mapped_column(BigInteger)
    source_metadata: Mapped[dict] = mapped_column(JSONB)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ImportAttempt(Base):
    __tablename__ = "import_attempt"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.import_batch.id"))
    filename: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSONB)


class SourceRow:
    """将原始行直接保存在所属证据表，避免额外七百万行通用中间表。"""
    source_file_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    record_no: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    context_id: Mapped[int] = mapped_column(BigInteger)
    source_row_id: Mapped[str] = mapped_column(Text)
    raw_fields: Mapped[dict] = mapped_column(JSONB)

    @declared_attr.directive
    def __table_args__(cls):
        return (
            ForeignKeyConstraint(["source_file_id", "context_id"],
                                 [f"{SCHEMA}.source_file.id", f"{SCHEMA}.source_file.context_id"]),
            UniqueConstraint("source_file_id", "source_row_id"),
            CheckConstraint("record_no > 0", name=f"ck_{cls.__tablename__}_record_positive"),
        )


class EntityRecord(SourceRow, Base):
    __tablename__ = "entity_record"
    source_entity_id: Mapped[str] = mapped_column(Text)
    name_raw: Mapped[str] = mapped_column(Text)
    entity_kind: Mapped[str] = mapped_column(Text)
    semantic_status: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        ForeignKeyConstraint(["source_file_id", "context_id"], [f"{SCHEMA}.source_file.id", f"{SCHEMA}.source_file.context_id"]),
        UniqueConstraint("source_file_id", "source_row_id"),
        UniqueConstraint("context_id", "source_entity_id"),
        UniqueConstraint("context_id", "name_raw"),
        CheckConstraint("entity_kind IN ('standard_css_candidate','non_css_unresolved')", name="ck_entity_kind"),
    )


class GeneLocus(Base):
    """仅标准 CSS 形式的可查询投影，semantic_status 不因此升级为 confirmed。"""
    __tablename__ = "gene_locus"
    context_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    css_id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_entity_id: Mapped[str] = mapped_column(Text)
    semantic_status: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        ForeignKeyConstraint(["context_id", "source_entity_id"], [f"{SCHEMA}.entity_record.context_id", f"{SCHEMA}.entity_record.source_entity_id"]),
        UniqueConstraint("context_id", "source_entity_id"),
        CheckConstraint("css_id ~ '^CSS[0-9]{7}$'", name="ck_gene_css_format"),
    )


class EntityEvidence(SourceRow):
    source_entity_id: Mapped[str] = mapped_column(Text)
    lexical_status: Mapped[str] = mapped_column(Text, default="matched")
    semantic_status: Mapped[str] = mapped_column(Text, default="pending")

    @declared_attr.directive
    def __table_args__(cls):
        return (
            ForeignKeyConstraint(["source_file_id", "context_id"], [f"{SCHEMA}.source_file.id", f"{SCHEMA}.source_file.context_id"]),
            ForeignKeyConstraint(["context_id", "source_entity_id"], [f"{SCHEMA}.entity_record.context_id", f"{SCHEMA}.entity_record.source_entity_id"]),
            UniqueConstraint("source_file_id", "source_row_id"),
            Index(f"ix_{cls.__tablename__}_entity", "context_id", "source_entity_id"),
        )


class AnnotationRecord(EntityEvidence, Base):
    __tablename__ = "annotation_record"
    anno_id: Mapped[str] = mapped_column(Text, index=True)
    anno_type: Mapped[str] = mapped_column(Text)
    anno_name: Mapped[str] = mapped_column(Text)


class BlastHit(EntityEvidence, Base):
    __tablename__ = "blast_hit"
    accession: Mapped[str] = mapped_column(Text, index=True)


class DomainHit(EntityEvidence, Base):
    __tablename__ = "domain_hit"
    accession: Mapped[str] = mapped_column(Text, index=True)
    annotation_type: Mapped[str] = mapped_column(Text)
    method_raw: Mapped[str] = mapped_column(Text)


class FactorAssignment(EntityEvidence, Base):
    __tablename__ = "factor_assignment"
    factor_type: Mapped[str] = mapped_column(Text)
    family_raw: Mapped[str] = mapped_column(Text, index=True)


class SampleRecord(Base):
    __tablename__ = "sample_record"
    source_file_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.source_file.id"), primary_key=True)
    sample_id_raw: Mapped[str] = mapped_column(Text, primary_key=True)
    metadata_raw: Mapped[dict] = mapped_column(JSONB)
    semantic_status: Mapped[str] = mapped_column(Text)


class RawObservation(EntityEvidence, Base):
    __tablename__ = "raw_observation"
    sample_id_raw: Mapped[str] = mapped_column(Text)
    value_raw: Mapped[str] = mapped_column(Text)
    value_numeric: Mapped[object | None] = mapped_column(Numeric())
    measurement_kind: Mapped[str] = mapped_column(Text)


# 样本仅在来源文件内唯一；相同编号不跨实验、跨快照自动合并。
RawObservation.__table__.append_constraint(ForeignKeyConstraint(
    ["source_file_id", "sample_id_raw"], [f"{SCHEMA}.sample_record.source_file_id", f"{SCHEMA}.sample_record.sample_id_raw"]))
Index("ix_raw_observation_sample", RawObservation.context_id, RawObservation.source_entity_id, RawObservation.sample_id_raw)


class GeneSequence(EntityEvidence, Base):
    __tablename__ = "gene_sequence"
    protein: Mapped[str] = mapped_column(Text)
    transcript: Mapped[str] = mapped_column(Text)
    gene: Mapped[str] = mapped_column(Text)
    upstream: Mapped[str] = mapped_column(Text)
    downstream: Mapped[str] = mapped_column(Text)
    cds: Mapped[str] = mapped_column(Text)


class NetworkDataset(Base):
    __tablename__ = "network_dataset"
    source_file_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.source_file.id"), primary_key=True)
    code_raw: Mapped[str] = mapped_column(Text)
    metadata_raw: Mapped[dict] = mapped_column(JSONB)
    semantic_status: Mapped[str] = mapped_column(Text)


class NetworkEdge(SourceRow, Base):
    __tablename__ = "network_edge"
    tag1_raw: Mapped[str] = mapped_column(Text)
    tag2_raw: Mapped[str] = mapped_column(Text)
    person_r_raw: Mapped[str] = mapped_column(Text)
    person_p_raw: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        ForeignKeyConstraint(["source_file_id", "context_id"], [f"{SCHEMA}.source_file.id", f"{SCHEMA}.source_file.context_id"]),
        ForeignKeyConstraint(["context_id", "tag1_raw"], [f"{SCHEMA}.entity_record.context_id", f"{SCHEMA}.entity_record.name_raw"]),
        ForeignKeyConstraint(["context_id", "tag2_raw"], [f"{SCHEMA}.entity_record.context_id", f"{SCHEMA}.entity_record.name_raw"]),
        ForeignKeyConstraint(["source_file_id"], [f"{SCHEMA}.network_dataset.source_file_id"]),
        UniqueConstraint("source_file_id", "source_row_id"),
        Index("ix_network_edge_tag1", "context_id", "tag1_raw", "source_file_id"),
        Index("ix_network_edge_tag2", "context_id", "tag2_raw", "source_file_id"),
    )


class MappingRow(SourceRow, Base):
    __tablename__ = "mapping_row"
    candidate_css_id: Mapped[str | None] = mapped_column(Text, index=True)
    transform_rule: Mapped[str] = mapped_column(Text)
    semantic_status: Mapped[str] = mapped_column(Text)


class MappingMember(Base):
    __tablename__ = "mapping_member"
    source_file_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    record_no: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    namespace_raw: Mapped[str] = mapped_column(Text, primary_key=True)
    identifier_raw: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        ForeignKeyConstraint(["source_file_id", "record_no"], [f"{SCHEMA}.mapping_row.source_file_id", f"{SCHEMA}.mapping_row.record_no"]),
        Index("ix_mapping_member_lookup", "namespace_raw", "identifier_raw", "source_file_id"),
    )


ROW_MODELS = {
    "catalog": EntityRecord, "annotation": AnnotationRecord, "blast": BlastHit,
    "domain": DomainHit, "factor": FactorAssignment, "expression": RawObservation,
    "sequence": GeneSequence, "network": NetworkEdge, "mapping": MappingRow,
}
