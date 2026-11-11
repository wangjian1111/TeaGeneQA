# TeaGeneQA V1.1

基于导师提供的 TPIA CSV，实现版本化数据导入、标准 CSS 位点精确查询、多源注释汇总和原始记录追溯。后端使用 FastAPI / SQLAlchemy 2 / PostgreSQL，页面使用 Streamlit；受控问题查询使用规则和模板，无需 LLM API。

当前参考版本与样本语义仍有未决项。所有来源 ID 保持文本，`val` 保留原字符串；13 个非 CSS 命名实体独立保存。检索命中、GO/KO、PFAM/IPR、BLASTP 和相关网络均不作为实验验证或因果结论。

V1.1 增加独立候选评测集、分层数据可靠性验证、隔离 Compose/性能基线和 8 个本地只读工具接口；没有接入外部 LLM 或完整 RAG/Agent。候选题尚未经生物学专家审核。参见[项目架构与数据字典](reports/10_teageneqa_feishu_overview.md)、[工具契约](docs/tool_calling.md)和[评测复跑方法](evaluations/v1_1/README.md)。

GitHub 仓库包含代码、迁移、测试和配置示例。实验室原始 CSV、带真实记录的候选题集、数据库及本地验收产物单独保管。获得数据使用许可后，把相应 CSV 放入 `data/raw/`，再按下文导入。没有 CSV 时可以启动空库服务和检查健康接口，实际基因查询需要完成导入。

## 快速运行：Docker 数据库 + 现有 llm-study 环境

从项目根目录执行。当前已验证 Python 3.11.15；不需要重建现有环境。

新克隆的项目先运行 `python scripts/setup_compose_env.py` 生成本地配置；数据库迁移可在空库上完成，最后一行导入命令需要原始 CSV。

```bash
conda activate llm-study
python -m pip install -r requirements-dev.txt
docker --context desktop-linux compose up -d --wait db
python -m alembic upgrade head
python -m teageneqa.importer --manifest config/import_manifest.json
```

先启动 Docker Desktop。当前数据库目标为 Docker 的 `127.0.0.1:55433/teageneqa_dev`，Python 自动读取项目根目录 `.env` 中的密码与端口；无需在终端输入带密码的连接串。本机已有配置请直接复用，新电脑先运行 `python scripts/setup_compose_env.py` 生成配置。已迁入的数据无需重新导入；重跑导入器会增加运行审计日志，业务记录保持幂等。

API 与 Streamlit 在现有 `llm-study` 环境运行，只有 PostgreSQL 位于 Docker。完整应用容器部署仍是可选方案，见 [部署说明](docs/deployment.md)。原生 `.runtime/pgdata` 数据保留，显式使用 `TEAGENEQA_DATABASE_MODE=native` 可切回 55432；默认不会因 Docker 连接失败自动退回原生库。

分别在两个终端启动服务：

```bash
conda activate llm-study
python -m uvicorn teageneqa.api:app --host 127.0.0.1 --port 18000
```

```bash
conda activate llm-study
python -m streamlit run ui/app.py --server.address=127.0.0.1 --server.port=18501 --browser.gatherUsageStats=false
```

- 页面：[http://127.0.0.1:18501](http://127.0.0.1:18501)
- OpenAPI：[http://127.0.0.1:18000/docs](http://127.0.0.1:18000/docs)
- 健康检查：[http://127.0.0.1:18000/api/v1/health](http://127.0.0.1:18000/api/v1/health)

服务只监听本机。8000 端口已有其他服务，本项目使用独立的 18000/18501；不停止其他项目服务。停止 API/页面用其终端 Ctrl+C，停止 Docker 数据库用 `docker --context desktop-linux compose stop db`，命名卷数据保留。不要执行 `down -v` 删除数据卷。

## 数据导入与未来数据

导入清单 [config/import_manifest.json](config/import_manifest.json) 明确指定上下文、来源文件、类型、原始数据集代码、可选预期哈希和未决元数据。相对 `raw_dir` 相对于清单文件目录解析；清单中的文件路径必须在该目录内。

当前上下文键 `tpia_css_audit_snapshot_20261103` 只是本地快照标识；2026-11-03 不是假定的数据发布日期。`source_genome_code_raw=3` 是原导出代码，不展开成未经确认的物种、材料或组装版本。

映射文件是否可向本上下文投影 CSS 候选，由清单显式声明 `context.metadata.mapping_anchor_namespace`。当前根据审计使用 SCZV2；未来清单未声明锚点时只保留原映射行与命名空间索引，不自动向本地基因投影。

```bash
python -m teageneqa.importer --manifest config/import_manifest.json --chunk-size 2000
python -m teageneqa.verification --manifest config/import_manifest.json
```

导入器先导入目录，再导入引用表。每个文件为一个事务，包含来源注册、原始记录与派生索引数据；失败时回滚整个失败文件，保留此前成功文件。重复执行会跳过相同内容与元数据的文件，不新增业务记录；每次执行仍保留独立的运行批次/尝试日志。

同一上下文的同一数据类型/数据集代码视为一个不可变来源槽位。如果新文件内容或元数据不同，导入器报告冲突并保留旧记录。未来新增或更正导出请新建清单和**新的 context.key**；即使字段结构、文件名、CSS 编号相同，也不能据此合并参考版本或实验。

错误报告位于 `reports/v1_runtime/imports/batch_<id>_errors.jsonl`，完整状态位于同目录的 `batch_<id>.json`。记录号是解析后的 CSV 数据记录序号，不含表头；原导出 `#` 和 `ID/id` 另行完整保存。检查编码、列宽、缺少字段、重复来源行 ID、未匹配引用、非法数值和读取期间文件变化。合法业务重复保留；PFAM/IPR 不按 gene+accession 或区间去重。

恢复只需修复新输入清单或其独立副本，然后重跑命令；不要修改原始快照以覆盖旧数据库。进程中断后，未提交文件由 PostgreSQL 回滚，下次运行会标记遗留批次 interrupted，跳过已提交文件并从失败文件的开头重读。暂不提供行内断点续传或跨文件整体事务。

## 主要 API

| 接口 | 作用 |
| --- | --- |
| `GET /api/v1/health` | 服务、数据库连通性和迁移版本 |
| `GET /api/v1/contexts` | 列出数据上下文与原始/未决元数据 |
| `GET /api/v1/genes/{gene_id}` | 标准 CSS 基本信息和来源 |
| `GET /api/v1/genes/{gene_id}/annotations` | GO/KO，支持 `annotation_type` |
| `GET /api/v1/genes/{gene_id}/blast` | 全部已有 BLASTP 记录，分页保留原始指标 |
| `GET /api/v1/genes/{gene_id}/domains` | PFAM/IPR，支持 `annotation_type=pfam/ipr` |
| `GET /api/v1/genes/{gene_id}/expression` | 原始值，支持 `sampleID=101` 等文本编号 |
| `GET /api/v1/genes/{gene_id}/profile` | 分证据类型汇总，独立分页，不预加载序列 |
| `GET /api/v1/genes/{gene_id}/factors` | TF/TR 及来源中其他原始分类 |
| `GET /api/v1/genes/{gene_id}/mappings` | 按候选基础 CSS 编号查原映射行 |
| `GET /api/v1/genes/{gene_id}/networks` | `dataset_code`、`direction=outgoing/incoming/both` 的原有序边 |
| `GET /api/v1/genes/{gene_id}/sequences/{sequence_type}` | 指定 Protein/Transcript/Gene/Upstream/Downstream/CDS 的原始序列 |
| `GET /api/v1/id-mappings` | `namespace`、`identifier`、可选 `target_namespace` 的多候选查询 |
| `GET /api/v1/entities/non-css` | 保留的非 CSS 实体目录 |
| `GET /api/v1/sources` | 来源文件、哈希、批次和数量 |
| `GET /api/v1/sources/{source_id}/records/{record_no}` | 完整原始字段和来源定位 |
| `POST /api/v1/query` | 白名单规则问答，禁止任意 SQL |
| `GET /api/v1/tools` | 工具参数、结果 JSON Schema 与证据边界 |
| `POST /api/v1/tools/{tool_name}` | 8 个白名单只读工具；参数严格校验，不接收 SQL |

列表接口使用 `limit=1..200`、`offset=0..1000000`；单个对象和健康检查不分页。`total` 是实际总量，`items` 是当前页，`source_file_count` 表示该上下文是否已导入该类文件。新增部分数据时，“尚未导入来源文件”和“已导入但本基因没有命中”分别提示。

基因 ID 严格匹配 `CSS[0-9]{7}`。`.1`–`.6` 保留在映射原编号中，不自动改成 `.1`。遇到多个上下文使用同一 CSS ID 返回 HTTP 409，调用方须显式传 `context_key`。无记录返回 404 或空分页列表；参数错误/未知参数返回 422；数据库不可用返回 503，不返回连接串或 SQL。

```bash
curl 'http://127.0.0.1:18000/api/v1/genes/CSS0008782/profile?context_key=tpia_css_audit_snapshot_20261103&limit=20'
curl 'http://127.0.0.1:18000/api/v1/genes/CSS0000001/expression?sampleID=101&context_key=tpia_css_audit_snapshot_20261103'
curl 'http://127.0.0.1:18000/api/v1/id-mappings?namespace=Biyun&identifier=GWHTAMJR007835&target_namespace=SCZV2&limit=20&context_key=tpia_css_audit_snapshot_20261103'
curl -X POST 'http://127.0.0.1:18000/api/v1/query' -H 'Content-Type: application/json' -d '{"question":"查询 CSS0000001 在 sampleID=101 的原始数值","context_key":"tpia_css_audit_snapshot_20261103"}'
```

## 测试与复核

```bash
conda activate llm-study
python -m pytest -q
TEAGENEQA_FULL_DATA=1 python -m pytest -q --junitxml=reports/v1_runtime/pytest.xml
python -m alembic check
```

测试需要可连接的 PostgreSQL 和创建临时数据库权限。普通测试建立随机 `teageneqa_test_<uuid>` 库，应用真实迁移，导入从真实 CSV 选取的夹具，结束只删除该随机库。生产/开发库不清空；不要把已有库名传给测试删除逻辑。`TEAGENEQA_TEST_ADMIN_URL` 可指定独立测试服务器；不设置则复用开发服务器的管理连接。

`TEAGENEQA_FULL_DATA=1` 额外对开发库进行只读全量数量与抽样字段核对。不开启时这一项明确 skip，其余测试仍可运行。UI 测试使用 Streamlit AppTest，HTTP 查询接到真实临时 PostgreSQL 上的 API，不硬编码预期回答。

没有原始 CSV、候选题集或 PostgreSQL 时，可以用占位连接串运行以下检查。该命令不连接数据库。完整套件还需要生成候选题集，见[评测复跑方法](evaluations/v1_1/README.md)。

```bash
DATABASE_URL=postgresql+psycopg://unused@127.0.0.1:1/unused \
  python -m pytest -q tests/test_db_config.py tests/test_v11_evaluation.py::test_oracle_does_not_import_system_under_test
```

部署、Schema 和数据语义边界见 [docs/deployment.md](docs/deployment.md)、[docs/schema.md](docs/schema.md)。初始验收、Docker 迁移和 V1.1 质量报告保留在研究工作区的 `reports/`。

## 当前范围

已有精确查询、来源追溯、分页、多候选映射、原序列与规则问答。未连接外部 LLM、未创建向量库、未实现组织/处理自动解释、差异表达、富集、调控/因果、正交关系证明或生产级身份认证。后续 RAG 先基于结构化证据和专家金标验证，不直接向量化大序列或整个数值矩阵。
