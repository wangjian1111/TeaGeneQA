# V1.1 候选问答集与专家终测模板

`candidates.jsonl` 有 50 条真实数据驱动候选题，10 类各 5 条。`manifest.json` 记录数据集 SHA-256、来源上下文与用途。它们属于本地评测产物，获得数据权限并导入后可按下文重新生成。`tool_schemas.json` 为仓库内机器可读的 8 个工具契约。候选题仅标记 development_candidate_only 和 not_reviewed_candidate，不能称为已审核金标。

每题包含 question_id、question、intent、expected_result、supporting_evidence、source_file、source_record_id、answerable、biological_review_status，以及用于结构化校验的 request、context_key 和类别。

expected_result 由 `scripts/v11_oracle.py` 的独立 psycopg 参数化 SQL 生成，再用原始 CSV 全文件计数/哈希和指定记录完整字段校验。Oracle 没有调用 qa.py、services.py、models.py、importer.py 或待测 API 生成答案。`scripts/v11_evaluate.py` 只读取冻结答案，比较实际意图、记录集合、原始值和来源。

answerable 的含义是当前数据能否给出所问的来源事实。“某来源没有该基因记录”可以用快照内空结果回答，并必须附快照限制；本地目录无此 ID 和缺少生物学证据的问题为 false。无数据/不可答问题没有伪造证据行，source_record_id 和 supporting_evidence 可以为空，absence_or_scope_reason 说明查询范围或缺口。

`expert_test_template.jsonl` 是 **10 个空白题目槽位，不是 10 条已完成的专家测试题**。由导师填写真实问题、答案事实、证据定位、可答性、审核人和审核时间。保留 expert_final_holdout 标记，不得从候选题直接复制为终测题，不得用于提示词调优。正式评价前须补齐证据、检查与开发集的问题/基因/映射组重合、冻结文件哈希，并隔离答案；当前没有正式专家终测分数。

本轮没有依据候选题修改 V1 的规则或提示词。33/50 等结果是开发候选集上的工程基线，包含已知未覆盖意图，不是 LLM 准确率或生物学专家认可率。

## 复现

从项目根目录、llm-study 环境执行：

```bash
# 重新生成前先确认这是同一个冻结原始快照；内容变化时会保存旧候选版本。
python scripts/v11_build_dataset.py

# 首次创建隔离环境；本机已有该环境时使用 resume。
docker --context desktop-linux build -t teageneqa:v1.1-quality .
python scripts/v11_deployment.py start

# 已有隔离环境的安全复跑入口
python scripts/v11_deployment.py resume
python scripts/v11_run_tests.py
python scripts/v11_evaluate.py
python scripts/v11_benchmark.py --requests 120 --rps 25
python scripts/v11_deployment.py persistence
python scripts/v11_deployment.py stop
```

`start` 和 `resume` 按当前状态选其一。隔离环境使用独立项目名、卷和 18110/18511/55434 端口；状态位于 reports/v1_1_runtime/deployment_state.json，独立密码只在被忽略的 .runtime 下、0600 权限文件中。start 拒绝覆盖已有状态；stop 保留数据卷。

性能基线只允许该登记环境，默认并发 1/4、每组合 120 次、预热 5 次不计入统计、目标平均 25 RPS，允许至多 4 条的微批量突发，并记录实际峰值在途数。不会对 18000/18501/55433 执行负载测量。

报告位于 reports/v1_1_runtime：data_reliability.json、qa_evaluation.json、performance.json、performance.csv、performance_requests.jsonl、deployment_validation.json、pytest.xml 等。详细汇总见 reports/08_v1_1_quality_review.md。
