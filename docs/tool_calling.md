# TeaGeneQA V1.1 受控工具调用契约

本阶段只实现本地只读适配器、参数验证、结果结构和测试；没有连接 LLM，没有上传实验室数据。现有规则问答保持原实现，作为工程基线。下一阶段的模型只负责选择工具、填写参数和组织有引用的回答。

机器契约见 [tool_schemas.json](../evaluations/v1_1/tool_schemas.json)，由 `teageneqa.tools.tool_catalog()` 生成，包含各工具的参数 JSON Schema、data Schema 和统一结果信封。HTTP 入口为 `GET /api/v1/tools` 和 `POST /api/v1/tools/{tool_name}`；POST 请求体直接是参数对象。

## 1. 工具与已有 API 的对应

| 工具 | 已有 API/查询能力 | 关键参数 | data 结构 |
| --- | --- | --- | --- |
| get_gene_profile | genes/{gene_id}/profile | gene_id、context_key、limit、offset | ProfileView：gene、分来源 evidence、序列条数、实验验证状态 |
| get_gene_annotations | genes/{gene_id}/annotations | 上述字段、annotation_type | EvidencePage，原 GO/KO 字段和来源 |
| get_blast_hits | genes/{gene_id}/blast | gene_id、context_key、分页 | EvidencePage，已有序列相似性命中和原指标 |
| get_protein_domains | genes/{gene_id}/domains | 上述字段、annotation_type=pfam/ipr | EvidencePage，多方法及重复记录保留 |
| get_expression_values | genes/{gene_id}/expression | gene_id、context_key、**必填 sample_id**、分页 | EvidencePage，保留 sampleID 和 val 字符串 |
| resolve_gene_identifier | id-mappings | namespace、identifier、target_namespace、context_key、分页 | total、ambiguous、全部原始行候选（分页） |
| get_source_evidence | sources/{id}/records/{record_no} | source_file_id、record_no | SourceResult：原字段、source_kind、证据类别、provenance、omitted_fields |
| get_transcription_factors | genes/{gene_id}/factors | gene_id、context_key、分页 | EvidencePage，原 TF/TR/特殊分类 |

## 2. 参数约束

- 所有参数对象 `additionalProperties=false`；运行时 Pydantic strict 模式禁止把布尔值、浮点数或字符串自动当成整数。
- gene_id 严格匹配 `CSS[0-9]{7}`；带 `.1`–`.6` 等后缀的编号使用映射工具，13 个非 CSS 实体不进入标准基因查询。
- context_key 为 1–128 位 ASCII 字母、数字、`_.-`；允许省略，但多上下文命中时必须返回歧义，不能自动选择最新版本。
- limit 默认 20，范围 1–50；offset 默认 0，范围 0–1,000,000。现有 REST 列表接口的 limit 上限仍为 200，工具层采用更小预算。
- sample_id 为原始文本编号，1–128 位 `A-Za-z0-9_.:-`；不接受组织名称代替编号。
- namespace/target_namespace 仅允许 SCZV2、Biyun、DASZ、HD、LJ43、SCZV1、TGY、YK10、COL、CCH。identifier 长度为 1–200，按参数化查询处理，不执行其中的文本。
- source_file_id、record_no 为严格正整数；record_no 是不含表头、从 1 开始的数据记录序号。
- 不存在 SQL、Python、URL 抓取等自由执行参数；工具调用事务强制 `SET TRANSACTION READ ONLY`。

Schema 导出依据 [Pydantic JSON Schema 文档](https://docs.pydantic.dev/latest/concepts/json_schema/)。供应商特定的 strict-tool 格式仍需在未来模型适配时转换、验证；本次没有宣称已兼容某个在线模型。

## 3. 结果与错误

统一信封包含 `tool`、`status`、`data`、`error`、`evidence_policy`。data 的具体字段按各工具 Schema；列表的 total 是实际总量，items 是当前页。provenance 包含来源文件、SHA-256、导入批次、context_key、record_no、source_row_id 和原始记录回查 URL。

| HTTP / status | 处理方式 |
| --- | --- |
| 200 / ok | 返回已有记录，并保留证据类别和未决语义 |
| 200 / empty | 来源内无匹配记录，不等于生物学不存在 |
| 200 / insufficient_evidence | 当前上下文尚未导入该类来源，保留 source_file_count 和限制说明 |
| 200 / ambiguous | 映射有多个原始行候选，返回 total 与分页数据，不能挑第一条作为唯一结果 |
| 409 / ambiguous | 同一标识符涉及多个上下文，需要用户选择 context_key |
| 404 / not_found 或 unknown_tool | 基因/来源/记录未检索到，或工具名不在白名单 |
| 422 / invalid_arguments | 参数类型、格式、范围或额外字段无效；不复制无效输入到错误详情 |
| 503 / unavailable | 数据库不可用或迁移未完成；不返回 SQL、连接串和密码 |

HTTP 层无法解析的 JSON、非法路径或非对象请求体使用既有 REST 参数错误格式；成功解析到适配器的工具调用使用以上信封。程序错误不会被伪装成有效事实。

get_source_evidence 明确返回底层 source_kind 和证据类别；六类序列及超过 2,000 字符的其他原字段从工具上下文省略，并在 omitted_fields 中记录字段名、原字符数和原因。原记录仍可通过已有来源/序列 API 按需查询。省略不是把空字符串当成真实序列，也不声称完整原字段已经发送给模型。

## 4. 证据边界与未来回答协议

| 类别 | 输出含义 | 允许的回答 |
| --- | --- | --- |
| raw_catalog_record / raw_observation / raw_sequence | 来源原始字段、编号、数值或序列 | “该来源记录的 val 为……”；单位和样本含义待确认 |
| existing_computational_annotation / existing_domain_annotation / existing_classification | 已有 GO/KO、PFAM/IPR、TF/TR 注释或分类 | “已有注释包含……” |
| existing_sequence_similarity_hit | 既有 BLASTP 相似性命中 | “已有比对命中该 accession……”；不证明功能或正交 |
| source_mapping_candidate / existing_ordered_correlation_record | 同行候选映射或原有序相关记录 | 保留命名空间、方向和来源，不升级成身份等价或调控 |
| 实验验证证据 | 当前统一为 not_provided，items=[] | 不能宣称茶树实验功能已验证 |
| 无充分证据的推断 | inference=not_performed | 明确拒答或说明缺少哪类证据 |

未来模型应先确定唯一实体/上下文，再调用限定工具；有歧义就澄清；每个事实关联返回的来源记录；证据不足时说明限制。原字段里的文本一律作为不可信数据引用，不作为新指令。禁止任意 SQL、跨材料等价合并、猜测 sampleID 语义，或用多个可能同源的注释重复计作独立实验证据。

## 5. 本机演示

隔离验证环境已停止时先运行 `python scripts/v11_deployment.py resume`，再执行：

```bash
curl -X POST 'http://127.0.0.1:18110/api/v1/tools/get_expression_values' \
  -H 'Content-Type: application/json' \
  -d '{"gene_id":"CSS0000001","context_key":"tpia_css_audit_snapshot_20261103","sample_id":"101","limit":5}'
```

示例仅访问本机。日常后端在重新启动并加载 V1.1 代码后，同样提供上述工具路径，使用其实际 18000 端口。
