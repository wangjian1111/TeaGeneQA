# TeaGeneQA 项目架构与数据字典

TeaGeneQA 面向茶树基因资料查询与证据溯源。V1.1 工作区已接入 18 个 TPIA CSV，数据库收录 7,141,014 条原始记录。PostgreSQL 的 `teageneqa` Schema 包含 17 张业务表和 1 张 Alembic 版本表。

## 项目架构

```text
数据准备    原始 CSV ＋ 导入清单 → importer.py → PostgreSQL
在线查询    浏览器 → Streamlit → FastAPI → 查询服务 → SQLAlchemy / psycopg → PostgreSQL
结果返回    数据库结果行 → Python 字典与来源信息 → HTTP JSON → Streamlit 表格
```

| 模块 | 代码 | 职责 |
| --- | --- | --- |
| 查询页面 | `ui/app.py` | 收集 CSS 编号和筛选条件，发送 HTTP 请求，展示表格和来源。 |
| API 与数据结构 | `teageneqa/api.py`、`schemas.py` | 路由、参数校验、连接依赖、分页与 JSON 响应。 |
| 查询服务 | `teageneqa/services.py` | 按数据上下文定位实体，读取证据，汇总档案，回查来源记录。 |
| 规则与工具 | `teageneqa/qa.py`、`tools.py` | 规则入口解析文本并调用查询服务。V1.1 工具入口接受结构化参数，提供 8 个白名单只读工具。 |
| 存储与导入 | `db.py`、`models.py`、`importer.py`、`migrations/` | PostgreSQL 连接、表定义、可重复 CSV 导入与 Alembic 迁移。 |
| 质量验证 | `scripts/v11_oracle.py`、`v11_evaluate.py`、`v11_benchmark.py` | 独立比对数据库与 CSV，评测结构化 API、规则和工具，在隔离环境测量性能。 |

日常部署采用本机 `llm-study` 环境中的 Streamlit（18501）和 FastAPI（18000）。Docker PostgreSQL 使用宿主端口 55433，映射到容器 5432，命名卷负责持久化。V1.1 的完整 Compose 验证使用独立端口与卷。上次核对时日常 API 仍运行 0.1.0，重启后端后加载 V1.1 工具。自然语言入口当前使用规则与模板，外部 LLM、向量检索与完整 RAG/Agent 属于后续阶段。

## 一次查询的数据传输

1. Streamlit 收到 CSS 编号，发送 `GET /api/v1/genes/{gene_id}/profile`，携带 `context_key` 和分页参数。
2. FastAPI 校验输入，通过 `Depends(connection)` 取得数据库连接。服务层的 `gene_record()` 在指定上下文定位实体。
3. `profile()` 按证据类别调用 `evidence_page()`，读取功能注释、同源比对、结构域、分类、观测、映射和网络。序列按需读取。
4. SQLAlchemy 通过 psycopg 执行参数化 SQL。查询结果组装为 Python 字典，其中 `raw_fields` 保存原字段，`provenance` 保存来源定位。
5. FastAPI 按 `ProfileView` 校验并序列化为 JSON。Streamlit 用 `response.json()` 解析，从 `gene` 和 `evidence` 取值展示。

参数格式错误返回 422，目录未检索到返回 404，多上下文命中返回 409。上下文键限定本地来源快照，物种和参考组装信息仍按已确认程度保存。

## 基础 CSV 与数据库主表

下表为已核验快照的主表记录数。

| 原始 CSV | 数据库主表 | 记录数 | 内容及字段含义 |
| --- | --- | ---: | --- |
| `tpia_chrom_tag_css.csv` | `entity_record` | 50,538 | 实体目录。`ID` 为原始目录键，`GeneName` 为原始名称，保留位置和产物描述。 |
| `tpia_annoblastpdetail_css.csv` | `blast_hit` | 168,837 | 已有 BLASTP 命中。`geneID` 关联目录，`accession` 指命中条目，原区间及指标存入 `raw_fields`。 |
| `tpia_annopfamdetail_css.csv` | `domain_hit` | 302,525 | PFAM/IPR 注释。`iType` 区分类别，`mothod` 为来源方法字段原拼写，保留多方法及合法重复。 |
| `tpia_chrom_sequence_css.csv` | `gene_sequence` | 50,525 | `chrom_tag_id` 关联目录。Protein、Transcript、Gene、Upstream、Downstream、CDS 分列存储。 |
| `tpia_gene_annotation_css.csv` | `annotation_record` | 135,198 | GO/KO 原始注释。`GeneID` 关联目录，`AnnoID`、`AnnoName`、`AnnoType` 分别记录编号、名称和类别。 |
| `tpia_gene_expro_css.csv` | `raw_observation` | 2,527,342 | 按 `geneID`、`sampleID` 保存原始 `val`，保留字符串和数值投影。样本语义和单位待确认。 |
| `tpia_gene_id_convert_css.csv` | `mapping_row` | 50,525 | 保存 SCZV2、Biyun 等 10 个命名空间的同行原值，支持候选映射及反向查找。 |
| `tpia_tfactor_css.csv` | `factor_assignment` | 2,884 | `tag_id` 关联目录。保留 `tf_type`、`tf_name`、`tf_desc`，包括 TF/TR 及来源中的特殊分类。 |

## 相关网络 CSV

10 个文件合计 3,852,640 条边，统一写入 `network_edge`。每个文件在 `network_dataset` 中登记一条数据集，通过来源文件和数据集代码区分。

| 原始 CSV | dataset_code | 边数 |
| --- | --- | ---: |
| `tpia_ca_public_co_css.csv` | `ca_public` | 386,020 |
| `tpia_ectropis_co_css.csv` | `ectropis` | 369,630 |
| `tpia_eight_tissue_co_css.csv` | `eight_tissue` | 414,420 |
| `tpia_grayblight_co_css.csv` | `grayblight` | 413,260 |
| `tpia_leafhopper_co_css.csv` | `leafhopper` | 366,950 |
| `tpia_leafposition_co_css.csv` | `leafposition` | 391,200 |
| `tpia_meja_co_css.csv` | `meja` | 407,470 |
| `tpia_naci_co_css.csv` | `naci` | 355,610 |
| `tpia_peg_co_css.csv` | `peg` | 351,680 |
| `tpia_shadding_co_css.csv` | `shadding` | 396,400 |

`tag1`、`tag2` 分别保存为 `tag1_raw`、`tag2_raw`，保留原始顺序。`personR`、`personP` 分别保存为 `person_r_raw`、`person_p_raw`。其统计定义、筛选规则和实验条件待数据字典确认。`naci`、`shadding` 沿用来源拼写。

## 派生表、管理表与溯源

| 表 | 生成方式 | 作用 |
| --- | --- | --- |
| `gene_locus` | 从目录投影 50,525 个标准 CSS 形式记录 | 通过 `css_id` 定位目录内部 ID。13 个非 CSS 实体留在 `entity_record`，类别待确认。 |
| `sample_record` | 观测 CSV 按文件与 `sampleID` 提取 134 个编号 | 保存原始样本编号及待补充的组织、处理、重复等元数据。 |
| `mapping_member` | 映射 CSV 中 433,583 个非缺失单元格 | 按命名空间和原标识符反查候选行，保留多候选。 |
| `network_dataset` | 10 个网络 CSV 各一条登记 | 保存数据集代码、来源和待确认元数据。 |
| `source_file` | 18 个来源登记 | 保存文件名、SHA-256、编码、字段、行数和批次。 |
| `reference_context` | 导入清单声明的快照 | 限定查询与关联范围，保存物种与参考版本元数据及未决状态。 |
| `import_batch`、`import_attempt` | 导入程序执行时产生 | 保存批次与各文件导入、跳过和失败记录。 |
| `alembic_version` | Alembic 生成 | 记录数据库结构迁移版本。 |

标准 CSS 编号来自 `GeneName`，保存于 `gene_locus.css_id`。注释 `GeneID`、比对和观测 `geneID`、分类 `tag_id`、序列 `chrom_tag_id` 在同一 `context_id` 下对照目录原始 `ID`。证据表通过 `source_entity_id` 保存引用。网络端点按原始名称关联。

证据表的 `source_file_id` 关联 `source_file.id`。`record_no` 是不含表头、从 1 开始的 CSV 数据记录序号。`source_row_id` 保存 CSV 自带的 `ID/id`。多数原始字段保存在 `raw_fields`（JSONB），六类序列分列存为 TEXT。API 支持按文件和记录号回查原字段。

## 生物学解释边界

- `Genome=3` 对应物种、材料及参考版本，CSS/SCZV2 后缀的实体层级，均待来源确认。
- `sampleID` 对应的组织、处理、时点、重复与对照待确认。`val` 按原始观测展示，单位、归一化和零值含义待确认。
- GO/KO、PFAM/IPR 与 TF/TR 按来源注释或分类呈现。BLASTP 提供已有序列相似性证据，网络提供原始相关记录。当前快照的实验验证状态为 `not_provided`。
- BLASTP 的 `sLen`、`sStart`、`sEnd`、`mStart`、`mLen`、`mEnd`、`mIdentity`、`sIdentity`、`pValue` 沿用来源字段。两组坐标与指标的精确定义、比对库版本，以及 PFAM/IPR 的 `eValue` 与方法口径待确认。
- 同行映射可产生多个候选。系统保留命名空间、原后缀、上下文和来源，跨材料身份与正交关系待确认。

## 新手代码阅读路线

1. 从 `api.py` 的 `get_gene()` 开始。先看内层 `gene_record()` 如何定位，再看外层 `gene_view()` 如何组装。
2. 看 `api.py` 的 `connection()` 与 `db.py` 的 `make_engine()`，理解 `conn` 由 FastAPI 依赖注入提供。
3. 看 `services.py` 的 `gene_record()`。找到 `select()` 构造查询和 `conn.execute()` 执行查询的位置，再对照 `models.py` 中的 `GeneLocus`、`EntityRecord`。
4. 看 `gene_view()` 和 `provenance()`。追踪 `raw_fields`、文件名与记录号如何进入响应，对照 `schemas.py` 中的 `GeneView`。
5. 回到 `ui/app.py` 的 `call_api()`，看 `response.json()` 如何解析结果，再读 `profile()`、`evidence_page()` 和页面分页。
6. 接着读 `qa.py`、`tools.py` 和 `importer.py`，分别理解规则问答、工具校验与数据导入。

## 验收记录与项目内资料

V1.1 验收记录显示 90 项自动化测试通过。540 条分层记录完成字段与来源校验。50 条候选题供开发与回归，专家终测待审核。隔离环境完成 1,440 次性能测量，并验证数据库容器重建后来源记录数和数据卷保留。

项目内依据为 `README.md`、`docs/schema.md`、`docs/tool_calling.md`、`config/import_manifest.json`、`reports/08_v1_1_quality_review.md` 和 `reports/09_docker_database_migration.md`。
