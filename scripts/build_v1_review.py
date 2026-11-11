#!/usr/bin/env python3
"""把实际运行证据整理成实施审查报告，不编造性能或生物学验证。"""
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'reports/v1_runtime'


def read(name):return json.loads((OUT/name).read_text())
def table(headers,rows):
    def clean(x):return str(x).replace('|','\\|').replace('\n','<br>')
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+['| '+' | '.join(clean(x) for x in row)+' |' for row in rows])+'\n'


def main():
    data=read('data_verification.json');live=read('live_demo.json');db=read('database_summary.json');compose=read('compose_validation.json')
    xml=ET.parse(OUT/'pytest.xml').getroot();suites=xml.findall('testsuite');counts={key:sum(int(s.get(key,0)) for s in suites) for key in ['tests','failures','errors','skipped']}
    audit=json.loads((ROOT/'reports/audit_final/inventory.json').read_text())
    hashes={f['filename']:f for f in audit['files']}
    raw_checks=[]
    for entry in data['files']:
        path=ROOT/'data/raw'/entry['filename'];stat=path.stat();initial=hashes[entry['filename']]
        raw_checks.append({'filename':entry['filename'],'sha256_matches_first_audit':entry['file_sha256']==initial['sha256'],
                           'size_matches_first_audit':stat.st_size==initial['bytes'],'mtime_matches_first_audit':stat.st_mtime_ns==initial['mtime_ns']})
    preserved=all(all(r[k] for k in ['sha256_matches_first_audit','size_matches_first_audit','mtime_matches_first_audit']) for r in raw_checks)
    log=(OUT/'pytest.log').read_text()
    test_seconds=sum(float(s.get('time',0)) for s in suites)
    text=f'''# 07 TeaGeneQA V1 实施审查

审查日期：2026-11-09（Asia/Shanghai）。本报告根据实际 PostgreSQL、真实 CSV、pytest 和本机 HTTP 验收结果生成。标准环境：conda `llm-study` / Python 3.11.15 / PostgreSQL {data['database_version']}。没有调用外部模型或上传实验室数据。

**交付状态：本机可运行，真实数据已导入，{counts['tests']} 项测试中失败 {counts['failures']}、错误 {counts['errors']}、跳过 {counts['skipped']}。** Docker Compose 只完成静态验证，未在容器运行时执行。所有生物学功能仍按已有计算注释/原始记录表述，不宣称实验验证。

## 1. 实际完成的模块

| 模块 | 实现位置 | 实际状态 |
| --- | --- | --- |
| 来源上下文、目录与多源证据模型 | `teageneqa/models.py` | 17 张业务表与独立 teageneqa Schema 已迁移到开发库 |
| 初始迁移 | `migrations/versions/58a1592310ad_initial_versioned_source_evidence_schema.py` | upgrade head 已执行，alembic check 无新增操作 |
| 清单驱动流式导入 | `teageneqa/importer.py`、`config/import_manifest.json` | 18 文件首次成功；真实文件重跑全部 skipped |
| API 与查询工具 | `teageneqa/api.py`、`services.py`、`schemas.py` | 必需 7 个 GET 接口及扩展接口已实现、测试与 HTTP 运行验证 |
| 中文 Web 页面 | `ui/app.py` | CSS 搜索、上下文选择、分类表格、分页、样本过滤、序列与原记录追溯已实现 |
| 规则问答 | `teageneqa/qa.py` | 5 类白名单意图；无需 LLM API，拒绝任意 SQL/因果推断 |
| 数据复核 | `teageneqa/verification.py` | 逐文件全量行数/哈希和首中尾字段核对通过 |
| 本机数据库与部署 | `scripts/dev_postgres.py`、Dockerfile、compose.yaml | 原生服务已运行；Compose 官方 Schema 静态校验通过，未构建镜像 |
| 测试与文档 | `tests/`、README、`docs/` | PostgreSQL 随机临时测试库、Streamlit AppTest、独立真实开发库只读复核 |

页面：[{live['web_url']}]({live['web_url']})；API 文档：[{live['api_url']}/docs]({live['api_url']}/docs)。这些地址是验收时本机服务，进程退出后按 README 重启。已有 8000 端口服务未停止，本项目使用 18000/18501。

## 2. 数据库实际表结构

核心为 `reference_context → source_file → 各类原始证据行`。目录 `entity_record` 保存全部实体；`gene_locus` 只是标准 CSS 形式的可查询投影，状态仍 pending。不同上下文同名 CSS 不自动合并。

17 张表：reference_context、import_batch、import_attempt、source_file、entity_record、gene_locus、annotation_record、blast_hit、domain_hit、factor_assignment、sample_record、raw_observation、gene_sequence、network_dataset、network_edge、mapping_row、mapping_member。

每条原始证据共享 source_file_id、record_no、context_id、source_row_id、raw_fields。主键为来源文件+记录号，来源行 ID 在同文件内唯一；geneID/GeneID/tag_id/chrom_tag_id 与目录的外键只验证本上下文的词法引用存在，不宣称生物学层级已经确认。来源文件唯一槽位为 context+kind+dataset_code，内容改变需要新上下文，不覆盖旧快照。

BLAST 原始指标、PFAM/IPR 的 iType/mothod/区间/分值、TF 分类、val、网络原始相关量及全部映射列都保留。序列单独 TEXT 存储，不在 JSONB 重复存放，普通 profile 只取计数。source_record 的通用字段直接嵌入各证据表，避免增加七百万行中间表。完整列、约束、索引及 ER 图见 [实际 Schema 文档](../docs/schema.md)。

## 3. 已成功导入的数据量

- 来源文件：{db['source_files']} 个；标准 CSS 位点：{data['standard_css_loci']:,}；非 CSS 未决实体：{data['entity_counts']['non_css_unresolved']}。
- 原始记录：**{data['database_total_rows']:,}**，与 CSV 数据行数完全一致；该数字不包含派生索引/导入日志。
- sample_record：{db['samples']} 条，均按来源文件保留原始编号；网络数据集：{db['network_datasets']} 个。
- mapping_member：{data['mapping_members']['database_rows']:,} 条，对应十个材料/版本列的非候选缺失单元格，不代表相同数量的独立生物学映射证明。
- 数据库在复核时大小约 {data['database_bytes']/1024**3:.2f} GiB（含表、索引、数据库元数据，不含全部集群 WAL）；磁盘占用会随后续运行变化。

首次导入批次 {db['batches'][0]['id']}：{db['batches'][0]['started_at']} 至 {db['batches'][0]['finished_at']}。幂等性复跑批次 {db['batches'][1]['id']} 全部识别跳过；新增运行日志是审计历史，不是业务数据重复。

## 4. 原始 CSV 与数据库数量核对

'''
    text+=table(['CSV 文件','原始行数','数据库记录数','数量一致','哈希一致'],[[r['filename'],f"{r['csv_rows']:,}",f"{r['database_rows']:,}",r['count_matches'],r['hash_matches']] for r in data['files']])
    text+=f'''
核对口径：每个文件全量解析计数；数据库按来源文件精确 COUNT；每文件选首、中、尾三条，合计 {sum(len(r['sampled_records']) for r in data['files'])} 条，将完整原始字段与数据库字段比较，序列也按原字段恢复比较。**没有逐单元格比对全部七百万行**，全量字段保真仍由导入逻辑、约束、真实代表记录测试及抽样提供证据，不扩大验证范围。

原始文件与第一阶段审计的 SHA-256、大小、mtime 全部一致：**{preserved}**。`data/raw` 未修改、删除或覆盖。详细证据：[data_verification.json](v1_runtime/data_verification.json)、[raw_preservation.json](v1_runtime/raw_preservation.json)。

## 5. 已实现的 API

所有路径以 `/api/v1` 为前缀。必需接口 genes/{{gene_id}}、annotations、blast、domains、expression、profile、health 均已实现。扩展包含 contexts、factors、mappings、networks、按类型 sequences、id-mappings、non-css 实体和 sources/原始记录，以及 POST query。

'''
    text+=table(['实际 HTTP 验收路径','状态','总记录数（如适用）','本次返回'],[[r['path'],r['status_code'],r.get('total'),r.get('returned','单个对象')] for r in live['http_checks']])
    text+='''
列表统一使用 limit/offset，稳定按来源文件和记录号排序；total 不被当前页条数替代。profile 每类证据独立分页。source_file_count 区分“尚未导入该类文件”和“已导入但本基因无命中”。多个上下文同名 CSS 返回 409，错误参数/未知或重复参数返回 422，不存在 ID 返回 404，数据库不可用返回 503；连接串/SQL 不出现在错误响应。

## 6. 已运行的测试与结果

'''+f"执行：`TEAGENEQA_FULL_DATA=1 python -m pytest -q --junitxml=reports/v1_runtime/pytest.xml`。最终结果 **{counts['tests']} 项、失败 {counts['failures']}、错误 {counts['errors']}、跳过 {counts['skipped']}，耗时 {test_seconds:.2f} 秒**。这包括真实数据只读复核，不是模型效果评测或性能基准。\n\n"
    text+='''
测试覆盖：导入幂等、同快照内容冲突保留、跨 chunk 失败文件回滚和修复后重跑、编码/分隔符、缺文件/缺字段/重复来源 ID/非法数值报告、CSS 查询与无结果、GO/KO、多 BLAST 命中分页、PFAM/IPR 多方法及两条合法重复业务记录、反向映射全部候选和非 .1 后缀、13 非 CSS 隔离、sampleID/原始零值、来源哈希和原记录回查、版本歧义、序列按需、网络有序边、参数校验、数据库故障和规则拒答。

普通测试使用从真实 CSV 提取的小型夹具，临时库通过实际迁移建立，预期字段来自 CSV，不硬编码模型回答；异常注入只用于边界测试，不当作真实生物数据。随机测试库结束后删除；真实开发库仅做读取。Streamlit AppTest 的 HTTP 连接到该真实临时 PostgreSQL 上的 API，验证搜索和样本过滤后的表格内容；另使用真实浏览器检查本机页面、标签和来源提示。

当前环境出现两条依赖弃用警告：anyio 的 pytest CallSpec2 别名，以及 Starlette TestClient 对 httpx 的迁移提示。测试结果均通过，未为消除警告顺手升级其他项目依赖；后续应在独立兼容性变更中处理。

证据：[pytest.log](v1_runtime/pytest.log)、[JUnit XML](v1_runtime/pytest.xml)、[live_demo.json](v1_runtime/live_demo.json)、[Compose 静态校验](v1_runtime/compose_validation.json)。Compose runtime_executed=false，不能表述为容器部署验证通过。

## 7. 可实际演示的问题

'''
    text+=table(['问题','实际状态/意图','本机实际回答摘要'],[[r['question'],r['status']+'/'+str(r['intent']),r['answer']] for r in live['question_examples']])
    text+='''
也可演示 Biyun `GWHTAMJR007835` → SCZV2 的 114 条原始映射行候选：默认仅显示 20 条，通过分页取全；不把其中任一条当作唯一答案。CSS0000081 的 SCZV2 原始编号保留 `.2`，不改写成 `.1`。

## 8. 尚未实现的功能

- 未接外部 LLM、未构建向量库或文献 RAG；没有模型自由 SQL，也没有向第三方传输实验室数据。
- 未实现中文术语同义词检索、自然语言多基因/多意图推理、会话指代、GO 本体、KO 通路映射、富集或差异分析。
- 未实现已经确认的组织/处理解释、样本配对、基因—化学性状关联、正交/调控/因果证明或图像多模态。
- 未做容器构建/启动、生产部署、认证授权、TLS、定时备份、任务队列或行内断点续传。当前恢复粒度是文件。
- 同一上下文每种类型/数据集只能有一个不可变文件。未来增量或更正使用新上下文，不在本版本自动合并多个目录快照。

## 9. 已知问题与数据语义风险

1. Genome=3、CSS/SCZV2、数字后缀的实体层级、物种与参考版本仍待确认。上下文 metadata 保持 NULL/原始代码，semantic_status=pending。物理外键只保证目录词法引用存在。
2. 标准 CSS 的 50 个 sampleID 与 13 非 CSS 实体的 84 个 sampleID 没有编号交集。val 不解释成 TPM/FPKM/count 或化学浓度，两个观测块不自动配对。
3. PFAM 文件含 PFAM/IPR、多方法和 3 组去掉 #/ID 后的业务重复，均保留。TF 的 ARR-B/p15 分类未强制改成 TF/TR。
4. Transcript 与 CDS 全部相同；18,641 行 Gene 长度短于 end−start+1。系统提供原序列，不声称序列坐标或翻译完整性已证明。
5. 原网络每个 tag1 恰 10 条，并不完整/对称。personR/personP 的方法、样本量和精度未确认，不用于宣称调控或抗性。
6. POST query 是白名单规则最小版本，非自由生物学问答。只支持一个完整 CSS ID 的单一查询意图；复杂问题可能拒答/要求澄清，尚无独立自然语言盲测准确率。
7. 本机服务使用开发配置，仅绑定环回地址；无身份认证。PostgreSQL 工具安装在本项目，未设置系统自启。Compose、迁移降级和生产运维的未验证范围如上。

## 10. 下一阶段 RAG 与科研实验建议

先获得导师确认的参考/实体/sample 字典和真实问题金标，再围绕 B/C 的混合检索与证据忠实回答开展比较；A 的命名空间/后缀归一化作为基础模块。D 等样本元数据到位后扩展，E 等配对和真正多模态数据到位后再考虑。

下一阶段优先建立 30–50 条本地查询验收题及独立专家盲测题，比较参数化 SQL/规则、词法检索、dense、混合检索，以及固定证据下的来源标记/逐 claim 引用/拒答校准。按基因和映射组划分，防止重复目标 ID 泄漏；分别测检索召回、结果集合/数值正确、引用支持与答案覆盖，保留文件哈希、证据记录号、模型/提示版本。

大序列和数值矩阵继续走专用查询；RAG 只解释检索工具返回的证据。新增外部模型需另行授权数据传输。当前可报告的是工程行为与数据保留通过验证，不能以此声称论文创新、模型质量或新基因功能。

部署入口：[README](../README.md)、[部署说明](../docs/deployment.md)、[Schema](../docs/schema.md)。第一阶段 00–06 报告保持原样，作为语义边界和后续研究计划依据。
'''
    preview=OUT/'ui_preview.png'
    if preview.exists():text+='\n页面预览：[实际 Streamlit 截图](v1_runtime/ui_preview.png)。\n'
    else:text+='\n浏览器已完成页面与标签的语义状态检查；截图导出超时，未将截图作为验收或交付证据。\n'
    (ROOT/'reports/07_v1_implementation_review.md').write_text(text,encoding='utf-8')
    (OUT/'raw_preservation.json').write_text(json.dumps({'all_preserved':preserved,'files':raw_checks},ensure_ascii=False,indent=2),encoding='utf-8')
    print('reports/07_v1_implementation_review.md',counts,'raw_preserved',preserved)


if __name__=='__main__':main()
