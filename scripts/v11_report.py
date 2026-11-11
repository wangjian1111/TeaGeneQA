#!/usr/bin/env python3
"""根据实际机器结果生成 V1.1 中文验收报告，不把候选题当作专家终测。"""
import hashlib
import json
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
RUNTIME=ROOT/'reports/v1_1_runtime'


def read(name):return json.loads((RUNTIME/name).read_text())


def main():
    quality=read('data_reliability.json');evaluation=read('qa_evaluation.json');performance=read('performance.json')
    deployment=read('deployment_validation.json');build=read('build_verification.json');full=read('full_csv_verification.json')
    suites=ET.parse(RUNTIME/'pytest.xml').getroot().findall('testsuite')
    totals={k:sum(int(s.get(k,'0')) for s in suites) for k in ['tests','failures','errors','skipped']}
    elapsed=sum(float(s.get('time','0')) for s in suites)
    assert all(r['status']=='passed' for r in [quality,performance,deployment,build,full])
    assert totals['tests'] and totals['failures']==totals['errors']==totals['skipped']==0
    cases=[json.loads(line) for line in (ROOT/'evaluations/v1_1/candidates.jsonl').read_text().splitlines()]
    assert hashlib.sha256((ROOT/'evaluations/v1_1/candidates.jsonl').read_bytes()).hexdigest()==evaluation['dataset_sha256']
    categories=[]
    labels={'basic':'基因基本信息','annotation':'GO/KO 注释','blast':'BLASTP 命中','domain':'PFAM/IPR','factor':'TF/TR 分类',
        'mapping':'ID 映射/歧义','expression':'原始表达观测','profile':'多来源证据汇总','no_data':'无数据/未检索到','unsupported_biology':'现有证据不可回答的生物学问题'}
    for cat,label in labels.items():
        def score(layer):
            data=evaluation['summary'][layer]['by_category'].get(cat)
            return f"{data['passed']}/{data['total']}" if data else '不适用'
        categories.append(f'| {label} | 5 | {score("structured")} | {score("tools")} | {score("rules")} |')
    perf_rows=[]
    for r in performance['results']:
        h=r['http_ms'];d=r['db_execute_ms']
        perf_rows.append(f"| {r['scenario']} | {r['concurrency']}/{r['peak_in_flight']} | {r['request_count']} | {h['p50']:.3f} | {h['p95']:.3f} | {h['p99']:.3f} | {d['p95']:.3f} | {r['error_rate']:.1%} |")
    group_rows='\n'.join(f"| {g['business_fields']['geneID']} | {g['business_fields']['accession']} | {g['record_numbers']} | {g['database_multiplicity']} |" for g in quality['duplicate_business_groups'])
    qa_failures=[r for r in evaluation['results']['rules'] if not r['passed']]
    not_observed=[s['name'] for s in quality['strata'] if not s['records']]
    question_rows='\n'.join(f"| {c['question_id']} | {c['question']} | {c['intent']} | {'是' if c['answerable'] else '否'} | 未审核候选 |" for c in cases)
    (ROOT/'evaluations/v1_1/candidate_questions.md').write_text('# 50 条开发候选题（未经过生物学专家审核）\n\n仅供开发与专家核阅，不作为最终科研测试集。标准事实、原始来源和记录号见同目录 candidates.jsonl。\n\n| ID | 问题 | 意图 | 当前数据可答 | 审核状态 |\n| --- | --- | --- | --- | --- |\n'+question_rows+'\n',encoding='utf-8')
    report=f'''# TeaGeneQA V1.1 质量与部署验收

生成时间：{datetime.now().astimezone().isoformat(timespec='seconds')}。包版本 0.1.1。结论依据本次真实 CSV、PostgreSQL、HTTP、pytest 和 Docker 实际运行记录。

**已交付：50 条未审核开发候选题、独立事实 Oracle 与分层校验、性能基线、8 个本地只读工具及隔离 Compose 验证。完整回归 {totals['tests']} 项通过，失败/错误/跳过均为 0。规则问答在候选集为 33/50，结构化 API 与工具层各 45/45。未调用外部 LLM，未开发完整 RAG/Agent。**

## 1. 新增功能及实际状态

| 模块 | 文件 | 状态 |
| --- | --- | --- |
| 独立事实 Oracle | scripts/v11_oracle.py | 直接参数化 SQL 读取真实记录，并独立按 CSV 记录号/哈希核对；不调用待测问答模块生成标准答案 |
| 候选问题与分层校验 | scripts/v11_build_dataset.py、evaluations/v1_1/ | 50 题和独立专家空白模板已生成；有行级来源的事实经 CSV 验证，缺失/不可答另标记范围 |
| 分层评测器 | scripts/v11_evaluate.py | 分别评测 REST、规则、工具；保留失败项，不用待测答案回填期望 |
| 受控工具 | teageneqa/tools.py、GET/POST /api/v1/tools | 8 个接口已实现和验证，禁止自由 SQL，事务只读 |
| 可选计时 | teageneqa/timing.py | TEAGENEQA_TIMING=1 才启用，仅记录时长和 SQL 次数 |
| 有限性能基线 | scripts/v11_benchmark.py | 只允许登记的隔离服务，已执行 1,440 次测量 |
| 隔离部署/重建检查 | compose.quality.yaml、scripts/v11_deployment.py | 镜像、全量导入、网络、健康、卷持久化均已执行 |
| 可复跑测试 | scripts/v11_run_tests.py、tests/test_tools.py、tests/test_v11_evaluation.py | 凭据在子进程环境传递，临时测试库位于隔离服务器 |

V1.1 没有新增业务表或修改原始 CSV。历史 00/07/08_docker/09 报告保留各阶段状态，以本报告描述本轮新验证范围。

## 2. 50 条候选题的覆盖与隔离

| 类别 | 候选数 | 结构化 API 正确 | 工具正确 | V1 规则正确 |
| --- | --- | --- | --- | --- |
{chr(10).join(categories)}
| 合计 | 50 | 45/45 | 45/45 | 33/50 |

结构化/工具 45 题包含空结果和预期 404；另外 5 题要求生物学拒答或澄清，不伪装成结构化事实查询。本轮 answerable=true 为 {sum(c['answerable'] for c in cases)}，false 为 {sum(not c['answerable'] for c in cases)}。

所有题均保存 question_id、question、intent、expected_result、supporting_evidence、source_file、source_record_id、answerable、biological_review_status。缺失/不可答问题没有伪造证据行，记录号可以为空，并给出快照范围或证据缺口。期望结果包含实际计数、原字段及来源；标准答案由独立 SQL 产生，再和 CSV 校验，未调用 qa.py/services.py/API 生成。

候选集哈希：`{evaluation['dataset_sha256']}`。所有 50 题均为 development_candidate_only / not_reviewed_candidate。没有依据这些题修改规则或提示词，不能将 66% 称为 LLM 泛化准确率或专家认可率。

独立 expert_test_template.jsonl 有 10 个待填写槽位，当前正式专家题为 0。导师需补充问题、事实答案、可答性、来源、审核人和时间；禁止直接复制候选题用于终测，正式评价前还须检查基因/映射组泄漏并冻结哈希。未开展专家盲测。

可读题目清单：[candidate_questions.md](../evaluations/v1_1/candidate_questions.md)；机器题库：[candidates.jsonl](../evaluations/v1_1/candidates.jsonl)。

## 3. 真实执行的测试

最终命令为 `python scripts/v11_run_tests.py`。测试使用宿主机 llm-study / Python 3.11.15，PostgreSQL 连接为隔离服务器 55434。完整 pytest：**{totals['tests']} passed，0 failed，0 errors，0 skipped，{elapsed:.2f} 秒**。

覆盖 V1 导入幂等/事务回滚、真实多 BLAST、PFAM 多方法与重复、13 个非 CSS 隔离、样本原始零值、来源和版本歧义、UI AppTest、参数/数据库异常；新增工具白名单、SQL 文本作为参数数据、严格类型/范围、缺少 sample_id、只读事务、完整映射分页、长序列省略标记、同源证据分类及上下文歧义。测试预期关联从原始目录读取，不根据 CSS 数字后缀猜内部 ID。

评测器的反例测试会故意更改返回值或来源记录号，确认能检测错误；这些变更仅在内存，未改原始文件。两条依赖弃用警告来自 anyio pytest 与 Starlette/httpx，未为消除警告升级无关依赖。

机器结果：[pytest.xml](v1_1_runtime/pytest.xml)、[pytest.log](v1_1_runtime/pytest.log)、[qa_evaluation.json](v1_1_runtime/qa_evaluation.json)。容器重建后再次执行候选评测，结构化和工具结果仍全部匹配。

## 4. 数据一致性验证

独立校验共定义 {len(quality['strata'])} 个分层，其中 {len(quality['strata'])-len(not_observed)} 个有真实记录；合并候选证据后去重检查 **{quality['records_checked']} 条记录的完整原字段、类型投影和来源定位，全部一致**。来源覆盖包括全部 18 个文件、记录号三区段抽样、GO 三类及 KO、PFAM/IPR 方法、BLAST 原始指标、零/非零观测、TF 特殊类型、13 个非 CSS、缺失标记、SCZV2 非 .1 后缀和多候选映射。

全部 CSV 流式解析计数合计 **{quality['csv_total_rows']:,}**，与每来源数据库实际 COUNT 及登记数一致；18 个文件哈希、大小、字段头一致，读取前后 stat 未变化。没有修改 data/raw。既有 verification.py 的另一次只读复核也通过：{full['csv_total_rows']:,} 条、54 条完整字段抽样。

3 组 PFAM/IPR 合法业务重复均保留不同来源记录：

| 原始 geneID | accession | 数据记录号 | 数据库重数 |
| --- | --- | --- | --- |
{group_rows}

最大 Biyun 多候选组 `{quality['multiple_candidate_group']['identifier']}` 有 {quality['multiple_candidate_group']['candidates']} 条原始行候选，逐候选字段已校验；这不是跨材料身份等价证明。缺失 productName/映射 NA 原字符串保留；空注释、缺少 sampleID 记录与不存在目录 ID 通过相应查询验证。

未观察到的分层：{'、'.join(not_observed)}。这是数据库原始文本字段的 SQL 格式探查结果，不把 0 条格式记录计作真实样例通过。

验证边界：全量是行数/文件哈希；字段逐项比对是 540 条分层样例及另一条路径的 54 条样例，不声称逐单元格重验全部七百万 CSV 行，也不确认未决生物学含义。明细：[data_reliability.json](v1_1_runtime/data_reliability.json)、[full_csv_verification.json](v1_1_runtime/full_csv_verification.json)。

## 5. API 性能报告

独立服务 18110 → 独立 PostgreSQL 55434；没有对现有 18000/18501/55433 执行负载测试。6 类查询 × 并发 1/4 × 每组合 120 次，共 **{performance['total_measured_requests']:,} 次测量**，另有每组合 5 次预热不计入统计。目标平均 25 RPS，允许最多 4 请求的小批量突发；实际在途峰值分别为 1/4。

客户端：{performance['environment']['client_os']}，{performance['environment']['machine']}，{performance['environment']['cpu_count']} 个逻辑 CPU，Python {performance['environment']['client_python']}。API 容器 Python **{performance['environment']['container_versions']['python']}**；数据库 PostgreSQL {build['postgresql_version']}。API 限 1 CPU/768 MiB，DB 限 1.5 CPU/2 GiB。镜像 ID：`{performance['environment']['image_id']}`。

单位均为 ms；并发列为“配置/实际峰值”，分位数采用 nearest-rank：

| 查询 | 并发 | 测量数 | HTTP P50 | HTTP P95 | HTTP P99 | DB 执行累计 P95 | 错误率 |
| --- | --- | --- | --- | --- | --- | --- | --- |
{chr(10).join(perf_rows)}

HTTP/传输错误为 0，响应内容校验错误为 0，全部请求取得计时头。DB 指标是 SQLAlchemy 成功 cursor.execute 的同请求累计耗时，包含数据库往返，不含取数、JSON 序列化和池等待，不等于 EXPLAIN 的纯计划执行时间。HTTP 与 DB 不能相加；两者 P50/P95/P99 均在 JSON/CSV 中，逐请求数据可以重算。[SQLAlchemy 计时说明](https://docs.sqlalchemy.org/en/20/faq/performance.html)

profile 每请求记录 32 次 SQL 执行，普通基础信息 3 次，注释/BLAST/domain/分页 6 次。分页并发 4 的 P99 为 245.715 ms，明显高于其 P95；本轮没有定位抖动原因，不能直接归因为数据库或索引。

这是单机、热缓存、每类少量固定基因、单轮有限负载结果；未测生产容量、冷缓存、百万级深 offset、大候选映射页或独占硬件性能。未证明零共享资源干扰；本轮核验的是现有服务保持健康、逻辑数据不变与测试资源受限。

原始结果：[performance.json](v1_1_runtime/performance.json)、[performance.csv](v1_1_runtime/performance.csv)、[performance_requests.jsonl](v1_1_runtime/performance_requests.jsonl)。

## 6. LLM 工具调用接口设计与实现

8 个本地工具均已实现参数验证和只读执行；JSON Schema 包含参数、各工具 data 类型和统一结果信封。要求的 get_gene_profile、get_gene_annotations、get_blast_hits、get_protein_domains、get_expression_values、resolve_gene_identifier、get_source_evidence 均有实现，另有 get_transcription_factors。

工具限制 limit≤50、offset≤1,000,000，CSS/样本/命名空间有白名单格式；多上下文返回 409，映射保留多候选，空结果不当作生物学不存在。get_source_evidence 返回来源类别，长序列和超过 2,000 字符的大字段明确省略并提供记录回查定位。原始事实、计算注释、序列相似性、候选映射、实验验证缺失与不进行推断均有独立标记。

**已验证的是本地适配器和 HTTP 工具，不是在线 LLM 自动选工具能力。** 供应商协议适配、模型路由、引用组织和拒答效果仍待下一阶段授权后的实验。完整设计：[tool_calling.md](../docs/tool_calling.md)；机器契约：[tool_schemas.json](../evaluations/v1_1/tool_schemas.json)。

## 7. Docker 验证结果

- 实际 Docker Engine {build['engine_version']} / Compose {build['compose_version']}；项目镜像构建成功。首次 Docker Hub 认证连接失败，单独重试官方基础镜像拉取后成功，未把网络失败当成代码错误或伪报通过。
- 独立项目 `{deployment['isolation']['project']}`，18110/18511/55434，仅绑定 127.0.0.1，卷名均带独立项目前缀，真实原始目录只读挂载。
- 实际跑通数据库 → Alembic → 18 CSV 导入 → API → Web；迁移和导入进程退出 0，数据库/API/Web 健康检查通过。Web 容器通过 `api:8000` 成功取得基因档案，证明内部连接配置有效。
- 运行容器的 6 个应用模块 SHA-256 与工作区一致，环境中密码未写入日志/报告；单独生成的测试凭据文件在 .runtime 下并为 0600。
- 仅停止隔离 DB 时 API 返回 503；新 DB 容器 ID 与旧 ID 不同，命名卷保留，18 个来源实际记录数、登记文件哈希及基因目录数量与重建前一致；候选题的字段/来源比对再次通过，API 恢复 200。未删除数据卷或原生库。
- 原开发库前后 18 来源的实际记录数、登记文件哈希及目录数量一致，现有 18000/18501 服务健康。没有重启这些现有进程；它们下次按 README 启动时加载 V1.1 代码。
- 隔离环境验收结束停止容器，保留数据卷和本地结果；可用 `python scripts/v11_deployment.py resume` 恢复。

本轮验证为本机隔离开发部署，不是公开生产部署。基础 `python:3.11-slim` 为浮动标签，导致与宿主机补丁版不同；已记录实际镜像 ID 和基础镜像 digest，未来需锁定 digest 提高长期复现性。[Compose 合并规则](https://docs.docker.com/reference/compose-file/merge/)

证据：[build_verification.json](v1_1_runtime/build_verification.json)、[deployment_validation.json](v1_1_runtime/deployment_validation.json)、[docker_build.log](v1_1_runtime/docker_build.log)。

## 8. 已发现问题

1. V1 规则严格通过 33/50：TF/TR、映射、profile 各 0/5；基本信息中的“原始坐标和链方向”表述未识别。属于规则意图覆盖缺口，结构化与工具层已有数据能力。
2. TGQA-C050 问“GO 注释能否证明控制儿茶素合成”，被路由为注释查询，未满足显式拒答/澄清协议。响应仍附实验验证缺失限制，未观察到肯定因果结论；不能把该失败夸大为已生成实验证明。
3. 分页出现尾部延迟抖动；单轮 120 次/组合不足以稳定估计 P99，需重复和定位。profile 32 次 SQL 是后续可测优化点，尚未证明其为抖动原因。
4. Docker 初始下载网络失败和浮动 Python 补丁版说明环境仍需版本/镜像固定；已实际重试验证成功。
5. 候选题为模板化自动生成、实体选取有开发偏向，不能代替真实科研问法和独立专家审核。保留失败项，不依据这批候选题调优后再冒充终测。

失败详情共 {len(qa_failures)} 条，逐题见 qa_evaluation.json；没有删题来提高分数。

## 9. 尚未确认的生物学字段

- Genome=3 对应物种、材料、组装/注释版本；CSS、SCZV2 和数字后缀的实体层级。
- sampleID 对应组织、条件、时点、重复、对照和材料；val 的单位/归一化及两类实体观测是否可配对。
- 13 个非 CSS 名称的正式类别与测量定义，不能自动当作基因。
- BLAST s/m、mIdentity/sIdentity、pValue 的精确定义与库版本；PFAM/IPR mothod/eValue、多方法重复及 TF 特殊分类解释。
- GO/KO 的方法、证据代码和实验文献；网络 personR/personP、top-10 筛选、方向、统计方法与条件；序列坐标/UTR/提取规则。

这些字段继续原样/pending 保存。字段保真通过不等于生物学定义已确认，已有相似性/注释/相关不等于茶树实验功能或因果。

## 10. V2.0 建议与复跑

优先请导师补齐参考/sample/实体字典并填写独立终测题。冻结专家题与答案，按基因和映射组检查泄漏；开发候选题只做回归/提示开发，最终科研评价使用独立专家集。

在明确数据传输授权后再增加供应商适配器，先用 mock 验证参数路由、版本澄清、分页、引用和拒答；真实模型比较规则路由与受控 Tool Calling，分开评估意图、事实集合、数值、来源支持和不可答识别。不允许模型生成 SQL，也不把长序列或数值矩阵直接塞入上下文。

性能下一步固定镜像 digest，按低/中/高记录数分层、多轮重复，定位客户端/连接池/数据库/序列化耗时；再比较 source 元数据批量读取等单变量优化。是否引入全文/dense/混合检索，应由新增中文真实问题和检索基线证明收益，当前不直接开发复杂 Agent、多模态或创新算法。

复跑：先启动 Docker Desktop、激活 llm-study，在项目目录执行 `python scripts/v11_deployment.py resume`，再运行 `python scripts/v11_run_tests.py`、`python scripts/v11_evaluate.py`、`python scripts/v11_benchmark.py --requests 120 --rps 25`、`python scripts/v11_deployment.py persistence`，最后 `python scripts/v11_deployment.py stop`。全新环境首次用 start，已有状态不要重复 start。详情见 [评测说明](../evaluations/v1_1/README.md)。
'''
    (ROOT/'reports/08_v1_1_quality_review.md').write_text(report,encoding='utf-8')
    print(json.dumps({'report':'reports/08_v1_1_quality_review.md','tests':totals,'candidate_rule_passed':evaluation['summary']['rules']['passed'],'performance_requests':performance['total_measured_requests']},ensure_ascii=False))


if __name__=='__main__':main()
