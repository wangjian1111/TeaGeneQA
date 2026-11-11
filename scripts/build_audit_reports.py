#!/usr/bin/env python3
"""从全量本地统计生成中文审计报告；解释层不改变原始数据。"""
import argparse
import json
from pathlib import Path


def cell(value):
    return str(value).replace('|','\\|').replace('\n','<br>').replace('\r','')


def table(headers, rows):
    return '\n'.join(['| '+' | '.join(map(cell,headers))+' |','| '+' | '.join(['---']*len(headers))+' |']+['| '+' | '.join(map(cell,row))+' |' for row in rows])+'\n'


def num(n):return f'{n:,}'
def pct(n):return f'{n*100:.2f}%'
def js(v):return json.dumps(v,ensure_ascii=False)


USES={
 'annoblastpdetail':'查询已有蛋白比对命中、外部蛋白描述和原始比对数值；不进行新 BLAST，不确证正交同源或功能。',
 'annopfamdetail':'按 PFAM/IPR 编号、名称、区间和分析方法查询结构域/家族注释；必须保留 iType 与 mothod。',
 'chrom_sequence':'按主表内部 ID 取蛋白、核酸及上下游序列；序列保存在结构化存储，不作为普通问答文本整段嵌入。',
 'chrom_tag':'本地实体目录、CSS 基因位置、结构和已有产物描述；13 条非 CSS 命名实体须单独保留且确认类别。',
 'gene_annotation':'GO 与 KEGG Orthology 原始注释查询、反查和证据汇总；不是完整通路、GO 层级或实验验证数据。',
 'gene_expro':'按 geneID/sampleID 查询原始 val；实际混有标准 CSS 实体和非 CSS 命名实体，单位、样本与测量类型待确认。',
 'gene_id_convert':'以 SCZV2 行为锚的多材料/版本标识符映射记录；反向查询返回集合，不保证一对一或同一实体。',
 'tfactor':'查询 TF/TR 等原始分类和家族，保留异常类别；不推断调控靶标或实验证明。',
}


def kind(f):return f['filename'].removeprefix('tpia_').removesuffix('_css.csv')
def use(f):return USES.get(kind(f),'查询该数据集代码下已有的基因对相关网络；文件名不能代替组织、处理、重复和统计方法元数据。')


def meaning(f, name):
    k=kind(f)
    fixed={
      '#':('已验证：本文件的导出记录序号，逐行等于 1..N。','不是基因 ID，不作为跨表键。'),
      'Genome':('已验证：本批所有记录值为文本 3。','3 对应的材料、组装/注释版本和物种代码需原始字典确认。'),
      'GeneName':('已验证：含 CSS 位点形式编号及 13 个非 CSS 名称。','字段名不意味着全部都是基因；也不是统一的功能 gene symbol。'),
      'chrom':('已验证：Chr.../其他序列名称文本。','可解释为参考序列/染色体名；组装版本和染色体命名规则待确认。'),
      'start':('已验证：整数形式的位置值。','基因区间起点的解释符合数据；0/1 基、闭开区间和所用组装仍待来源确认。'),
      'end':('已验证：整数形式的位置值。','基因区间终点；与 Gene 序列长度的一致性见序列复核，不能替代坐标制文档。'),
      'gType':('已验证：CSS 记录为 G，其他 13 条为空。','G 的正式枚举含义待确认。'),
      'strand':('已验证：+/- 两个方向符号。','通常表示链方向；负号不是缺失标记。'),
      'productName':('已验证：已有产物/蛋白描述，常带其他物种名及 PREDICTED 等文字。','可作为来源注释文本；描述中物种不是茶树记录的物种字段，不能据此认定实验证实。'),
      'AACount':('已验证：氨基酸计数字段的整数形式值。','与 Protein 长度有系统差值，是否包含终止密码子/其他定义待确认。'),
      'exonCount':('已验证：整数形式的外显子计数。','计数与分号区间列表的结构核对见质量检查；转录本选择方式待确认。'),
      'exonStarts':('已验证：单个位置或分号分隔的位置列表。','外显子起点列表；反链顺序可降序，不应当作数值污染。'),
      'exonEnds':('已验证：单个位置或分号分隔的位置列表。','外显子终点列表；与 exonStarts 按原序对应，坐标制待确认。'),
      'fivePrimeUTRStart':('已验证：空值、单个位置或分号位置列表。','5′ UTR 起点；缺失表示未提供记录，不证明无 UTR。'),
      'fivePrimeUTREnd':('已验证：空值、单个位置或分号位置列表。','5′ UTR 终点，不能强制整个字段转整数。'),
      'threePrimeUTRStart':('已验证：空值、单个位置或分号位置列表。','3′ UTR 起点；分段定义和坐标制待确认。'),
      'threePrimeUTREnd':('已验证：空值、单个位置或分号位置列表。','3′ UTR 终点；分段定义和坐标制待确认。'),
      'geneID':('已验证：十进制数字组成的内部标识符文本。','与 chrom_tag.ID 的词法引用已核对；本身不是 CSS/NCBI GeneID，生物学引用语义需来源字典。'),
      'GeneID':('已验证：注释表中的内部标识符文本，大小写保持原样。','词法可连接 chrom_tag.ID；不能只因为叫 GeneID 就解释为 NCBI GeneID。'),
      'tag_id':('已验证：分类表内部实体引用文本，当前唯一。','可词法连接 chrom_tag.ID；不等于该表 id。'),
      'chrom_tag_id':('已验证：序列表内部实体引用文本，当前唯一。','可词法连接 chrom_tag.ID；一行仅一组序列，不支持声称覆盖所有异构体。'),
      'sampleID':('已验证：101..234 的 134 个样本/观测编号文本。','组织、处理、时点、重复、样本配对及编号设计待确认；不推断编号含义。'),
      'val':('已验证：非负数字形式原始观测值，基因组与非 CSS 实体分属不同 sampleID 集合。','不能统一命名 TPM/FPKM/count 或代谢物浓度；单位、归一化与零值含义待确认。'),
      'AnnoID':('已验证：GO:七位数字或 K五位数字。','GO term / KEGG Orthology 编号；K 编号不是 KEGG pathway 编号。'),
      'AnnoName':('已验证：对应注释原始名称文本。','没有证据代码、文献或实验方法字段；保留前后空格原值。'),
      'AnnoType':('已验证：Cellular Component、Molecular Function、Biological Process、Kegg Orthology。','前三类对应 GO 分类，后一类为 KO；不能当作实验类型。'),
      'accession':('已验证：外部命中/结构域编号文本。','具体命名空间由所在表、iType、来源数据库版本共同确定。'),
      'name':('已验证：命中蛋白或结构域/家族名称原文。','是描述文本，不是本地基因的已确认功能名称。'),
      'sLen':('已验证：正整数形式的长度字段。','s 的确切对象需原始导出定义；与查询 Protein 长度的关系见补充统计。'),
      'sStart':('已验证：整数形式的比对起点字段。','s 所指序列、坐标制待确认。'),
      'sEnd':('已验证：整数形式的比对终点字段。','s 所指序列、坐标制待确认。'),
      'mStart':('已验证：整数形式的区间起点。','BLAST/PFAM 两表不能无条件共用语义；序列对象与坐标制待确认。'),
      'mEnd':('已验证：整数形式的区间终点。','命中区间终点的详细定义待确认。'),
      'mLen':('已验证：整数形式的长度字段。','不能未经文档确认为比对长度或目标全长。'),
      'mIdentity':('已验证：0..1 范围内的数值。','Identity 类指标的分母和定义待确认，不自行转换成 BLAST 标准 pident。'),
      'sIdentity':('已验证：0..1 范围内的数值。','与 mIdentity 的差别及分母待确认。'),
      'pValue':('已验证：BLAST 记录中的非负极小数或字面 0。','名称不足以判断为 P 值还是 E-value；保留原字段名，不将 0 解释为真实概率绝对为零。'),
      'eValue':('已验证：结构域表非负数字，含大于 1 的值。','不同分析方法的分值/缺省值规则需确认，不能一概按统一显著性阈值清洗。'),
      'iType':('已验证：pfam 或 ipr。','原表包含两类注释，不能把全部记录都叫 PFAM。'),
      'mothod':('已验证：原始拼写如此；含 Pfam/Gene3D/SUPERFAMILY 等方法名及 NA。','NA 的含义及各方法分值定义待确认；不自动更名覆盖原字段。'),
      'tag1':('已验证：有序记录的第一个 CSS 标识符。','每个 tag1 恰好 10 行，提示已筛选邻居表；不证明调控方向或完整网络。'),
      'tag2':('已验证：有序记录的第二个 CSS 标识符。','是原始边另一端；反向边可能不存在，不自动补边。'),
      'personR':('已验证：[-1,1] 内相关性形式数值。','疑似 Pearson r（官方工具存在 Pearson），本导出字段命名、计算方法和样本量仍待确认。'),
      'personP':('已验证：[0,1] 内极小数或字面 0。','疑似相关检验 P 值；是否校正、多重检验和精度规则待确认。'),
      'tf_type':('已验证：TF/TR/ARR-B/p15 四种值。','类别层级疑似混用，TR 展开含义及异常两类需导师/原始字典确认。'),
      'tf_name':('已验证：94 个分类/家族标签，如 TCP、MYB。','不是基因 symbol 或特异基因名称；家族分类版本待确认。'),
      'tf_desc':('已验证：家族/层级描述，含 ->。','层级表达规则和与 tf_name 的不完全一一对应待确认。'),
    }
    if name in ('ID','id'):
        return ('已验证：本文件中的唯一行标识符文本。','只有 chrom_tag.ID 是本批其他实体引用的候选目录键；其他表 ID/id 通常是记录 ID，不能跨表直接等值连接。')
    if name in SEQ_MEANINGS:
        return SEQ_MEANINGS[name]
    if k=='gene_id_convert' and name not in ('#','id'):
        return (f'已验证：列名为 {name} 的材料/版本命名空间标识符。','对应物种/材料公开命名可对照 TPIA；具体组装版本、后缀层级和映射算法须确认。每个单元格按原值保留，不推断正交关系。')
    return fixed.get(name,('已验证：原始字段值与统计见本行。','生物学定义待确认。'))


SEQ_MEANINGS={name:(f'已验证：{label}序列形式文本。',note) for name,label,note in [
 ('Protein','蛋白质','原始氨基酸序列；未进行翻译一致性、生物功能或实验验证。'),
 ('Transcript','转录本','本批与 CDS 字符串逐行完全相同，不能当作独立含 UTR 的全长转录本证据。'),
 ('Gene','基因区间核酸','长度与主表坐标跨度核对；并非整条染色体组装。'),
 ('Upstream','上游核酸','长度分布见统计；方向、截断规则和参考组装待确认，不推断为已证实启动子。'),
 ('Downstream','下游核酸','方向、长度及截断规则待确认。'),
 ('CDS','编码区核酸','长度模 3 与字符检查已做；未进行遗传密码表/翻译/开放阅读框生物学验证。')]}


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--audit-dir',type=Path,default=Path('reports/audit_final'));ap.add_argument('--reports-dir',type=Path,default=Path('reports'));args=ap.parse_args()
    root=args.reports_dir;adir=args.audit_dir
    inv=json.loads((adir/'inventory.json').read_text());rel=json.loads((adir/'relationships.json').read_text());follow=json.loads((adir/'followup.json').read_text())
    by={kind(f):f for f in inv['files']};files=inv['files'];co=[f for f in files if kind(f).endswith('_co')]
    field=lambda k,n:next(x for x in by[k]['fields'] if x['field']==n)
    links=rel['master_links'];groups=follow['expression_entity_groups'];checks=inv['checks']
    total=sum(f['rows'] for f in files);byte_count=sum(f['bytes'] for f in files);co_rows=sum(f['rows'] for f in co)
    standard=groups['standard_css']['entity_count'];entity_count=by['chrom_tag']['rows']
    seq_summary=dict(checks['sequence_crosschecks'])
    span=seq_summary.pop('gene_length_minus_inclusive_coordinate_span')
    seq_summary['gene_length_vs_end_minus_start_plus_one']={'equal_rows':span.get('0',0),'shorter_rows':sum(n for d,n in span.items() if int(d)<0),'longer_rows':sum(n for d,n in span.items() if int(d)>0),'minimum_difference':min(map(int,span)),'maximum_difference':max(map(int,span))}
    intro=f'> 审计日期：2026-11-03（Asia/Shanghai）。范围：本地 `data/raw/` 全部 {len(files)} 个 CSV，{num(byte_count)} bytes（{byte_count/1024**2:.2f} MiB），{num(total)} 条数据记录（不含表头）。原始数据未清洗、未覆盖、未上传。报告中的“可支持”表示数据可用于后续开发，系统功能尚未实现。\n\n'
    common='统计依据：[`audit_final/inventory.json`](audit_final/inventory.json)、[`audit_final/relationships.json`](audit_final/relationships.json)、[`audit_final/followup.json`](audit_final/followup.json)。原始记录号为 CSV 解析后的数据记录序号，不是物理行号。\n\n'
    def write(name,text): (root/name).write_text(text,encoding='utf-8')
    inventory_rows=[[f['filename'],num(f['bytes']),f'{f["bytes"]/1024**2:.2f}',num(f['rows']),f['columns'],use(f)] for f in files]
    summary_table=table(['文件','大小 bytes','MiB','数据行','列','潜在用途'],inventory_rows)
    # 01：每个文件和每个字段都落到实际扫描统计，避免只给代表性表结构。
    lines=['# 01 数据文件清单与质量审计\n\n',intro,common,'## 扫描与统计口径\n\n',
      '使用 Python 标准库 `csv`、`Decimal`、`sqlite3`，不安装依赖。CSV 严格解析，按 5,000 条记录批量写入本地审计暂存库，SQLite 缓存上限设为约 32 MiB；序列仅存 SHA-256 指纹并流式统计字符/长度。暂存库是可重建的审计产物，不是 PostgreSQL 业务库或数据库迁移。标识符始终作为文本保存；整数转换仅用于数值检查，不改写源 ID。\n\n',
      '分隔符通过候选嗅探或表头比较，再核对全文件列宽。编码通过全文件严格解码验证；ASCII-only 仅能确认为兼容 UTF-8，不能断言导出编码唯一。空串和大小写无关的 NA/N/A/NaN/NULL/None/\\N 作为“候选缺失”统计，原始标记按字段记录，尚不等同于来源语义定义。`-`/`.` 保留为原值，特别是 strand 的 `-` 是有效方向。数字 0 不是缺失。\n\n',
      '唯一数为排除上述候选缺失后的原始词法唯一数；不 trim、不大小写折叠、不去 ID 后缀。数字样式区分整数形式和小数/指数形式；二者混合不自动构成类型异常。数值极值使用 Decimal，避免极小数下溢。序列和整行重复使用 SHA-256 分组，存在理论哈希碰撞风险；异常候选另有原始字段复核。重复冗余行数 = Σ(每组行数−1)。序列分位数使用全量长度直方图的 nearest-rank，不是随机样本估计。\n\n',
      f'非 CSV 文件不参与数据统计：{js(inv["excluded_non_csv"])}。未发现数据字典、样本元数据或导出来源说明文件。完整指纹见附录。\n\n',summary_table,'\n## 按文件逐项审计\n\n']
    for i,f in enumerate(files,1):
        k=kind(f);header=f['headers']
        candidates=[c['field'] for c in f['fields'] if c['observed_unique_nonmissing']]
        lines += [f'### {i}. {f["filename"]}\n\n',f'- 规模：{num(f["rows"])} 行 × {f["columns"]} 列，{num(f["bytes"])} bytes；编码读取方式 `{f["encoding"]}`，BOM={f["bom"]}；{f["encoding_note"]}。\n',f'- 分隔符：{repr(f["delimiter"])}；{f["delimiter_method"]}；列宽异常 {len(f["malformed"])} 条。\n',f'- 完整字段：`'+ '`, `'.join(header)+'`。\n',f'- 无缺失且逐行唯一的列候选：{", ".join(candidates) or "无"}。仅说明本批数据唯一，不自动授予生物学主键语义。\n',f'- 用途与信息类型：{use(f)}\n\n']
        rows=[]
        for c in f['fields']:
            confirmed,pending=meaning(f,c['field'])
            samples='；'.join('`'+v+'`' for v in c['examples'])
            rows.append([c['field'],js(c['types']),f'{num(c["missing"])} / {pct(c["missing_ratio"])}',num(c['distinct_nonmissing']),samples,confirmed+' '+pending])
        lines += [table(['字段','实测词法类型（计数）','候选缺失数/率','非缺失唯一值','典型原值（截断）','含义与证据边界'],rows),'\n']
        numeric=[c for c in f['fields'] if c['numeric_min'] is not None]
        if numeric:
            lines += [table(['数值字段','最小值','最大值','字面零','负数','指数写法','float 下溢'],[[c['field'],c['numeric_min'],c['numeric_max'],c['zero'],c['negative'],c['scientific_notation'],c['float_underflow']] for c in numeric]),'\n']
        tokens={c['field']:c['missing_tokens'] for c in f['fields'] if c['missing_tokens']}
        whitespace={c['field']:c['surrounding_whitespace'] for c in f['fields'] if c['surrounding_whitespace']}
        lines += [f'候选缺失原始标记：`{js(tokens)}`。前后空白行数：`{js(whitespace)}`。缺失语义待确认；原始字符串未改写。\n\n',table(['重复口径','重复组数','冗余行数','最大重数'],[[label,d['duplicate_groups'],d['duplicate_excess'],d['max_multiplicity']] for label,d in f['duplicates'].items()]),'\n']
        if f['compound_keys']:
            lines += [table(['组合键候选','唯一组合数','重复组数','冗余行数','最大重数'],[[','.join(d['fields']),num(d['distinct_nonmissing']),d['duplicate_groups'],d['duplicate_excess'],d['max_multiplicity']] for d in f['compound_keys']]),'\n']
        if any(c['field'] in SEQ_MEANINGS for c in f['fields']):
            lines += [table(['序列列','长度 min/中位数/P95/max','平均长度','字符总数','允许字母表之外的记录','CDS 长度 mod3'],[[c['field'],' / '.join(str(c['sequence'][v]) for v in ['min_length','median_length','p95_length','max_length']),f'{c["sequence"]["mean_length"]:.2f}',num(c['sequence']['total_characters']),c['sequence']['invalid_alphabet_rows'],js(c['sequence']['cds_length_mod3'])] for c in f['fields'] if 'sequence' in c]),'\n']
        if checks.get(f['filename']):
            lines += ['专项检查（机器统计，定义见脚本）：\n\n```json\n'+json.dumps(checks[f['filename']],ensure_ascii=False,indent=2)+'\n```\n\n']
    lines += ['## 必须优先确认的质量与语义问题\n\n',
      f'1. 主表有 {num(standard)} 条标准 CSS 位点及 13 条非 CSS 命名实体（GA、GC、EGC、C、EGCG、EC、GCG、ECG、Theanine、Caffeine、Polyphenol、ester_catechins、non_ester）。后者坐标/序列缺失，但确有原始 val 记录，不能擅自删作坏数据，也不能算作茶树基因。其名称提示化学成分/汇总性状，正式名称、类别、单位和用途待导师确认。\n',
      f'2. 标准 CSS 记录形成 {num(standard)}×{groups["standard_css"]["sample_count"]}={num(groups["standard_css"]["rows"])} 完整观测块；非 CSS 记录形成 13×{groups["nonstandard_named"]["sample_count"]}={num(groups["nonstandard_named"]["rows"])} 完整块。两组 sampleID 交集为 {len(groups["sample_id_overlap"])}，不可按相同编号直接做基因—代谢物联合相关分析。\n',
      f'3. 10 张网络共 {num(co_rows)} 条有序记录，每个 tag1 恰为 10 条。很可能是邻居筛选输出（待确认具体算法），不构成完整或对称相关矩阵。\n',
      '4. PFAM 文件实际含 PFAM 与 IPR，多分析方法可能共享 accession/区间；去掉 # 与 ID 后有 3 组完全相同业务记录，保留原行及来源。eValue>1 不在缺少方法定义时直接判为错误。\n',
      '5. TF 表 tf_type 包含 6 条 ARR-B 与 4 条 p15，分类层级待核查，不把全部 2,884 条都称作 TF。\n',
      '6. Transcript 与 CDS 逐行相同；50,288 行 AACount=Protein长度+1、CDS长度=3×Protein长度+3，另 237 行两差值均为 0。Gene 序列在 31,884 行等于 end−start+1，在 18,641 行更短，最多短 31,599 字符；提取区间/UTR 规则或数据不一致须确认，不能称完整坐标—序列一致。这里未进行翻译或生物功能验证。\n',
      '7. 转换表 SCZV2 后缀分布：.1=49,793、.2=629、.3=83、.4=12、.5=6、.6=2；732 行不是 .1。按 id 查到的主表 CSS 基础编号在全部 50,525 行一致，基础编号错配为 0；732 只是非 .1 变体，不能称为误连。保留完整后缀，后缀实体层级及 id 的正式引用语义仍待确认。\n',
      '8. BLAST 的 sLen 与本地查询 Protein 长度在全部 168,837 行相等，这是字段对象的实测线索；s/m 的正式定义仍待确认。多数基因只有 5 条命中，不能保证完整搜索结果。GO:0009507 的原始名称有前导空白差异，trim 后均为 chloroplast，未发现该项语义冲突。leafhopper 网络 364,771/366,950 条的 |personR| 字面等于 1，需核实样本量、计算退化与输出精度，不推定为强生物学证据。\n\n',
      '补充精确统计（完整差值直方图只保存在机器 JSON，不在正文展开）：\n\n```json\n'+json.dumps({'expression_entity_groups':groups,'sequence_crosschecks':seq_summary,'annotation_term_label_conflicts':follow['annotation_term_label_conflicts'],'blast_hits_per_gene':follow['blast_hits_per_gene_distribution'],'domain_by_type_and_method':follow['domain_counts_by_type_method']},ensure_ascii=False,indent=2)+'\n```\n\n',
      '## 文件完整性与复现\n\n',table(['文件','SHA-256（前后相同）','mtime/size/hash 均保持'],[[f['filename'],f['sha256'],f['raw_unchanged']] for f in files]),'\n',
      '复现命令（从项目根目录运行；指定尚不存在暂存库的新输出目录）：\n\n```bash\npython3 scripts/audit_data.py --out-dir reports/audit_recheck\npython3 scripts/audit_followup.py --audit-dir reports/audit_recheck\npython3 scripts/build_audit_reports.py --audit-dir reports/audit_recheck\n```\n\n',
      '最后一条会重新生成 reports 下七份 Markdown 报告，复跑前自行保留需要比较的旧报告。当前脚本只读 raw 并写指定报告/审计目录；未创建 API、前端、向量库或业务数据库。审计暂存 SQLite 含本地派生字段，不外传。\n']
    write('01_data_inventory.md',''.join(lines))
    # 02：词法引用和实体语义分层。
    linkrows=[]
    for r in links:
        if r['field'] not in {'geneID','GeneID','tag_id','chrom_tag_id','nodes','SCZV2_candidate_base'}:continue
        linkrows.append([r['file'],r['field'],num(r['distinct']),num(r['matched']),pct(r['coverage_of_source']),pct(r['coverage_of_master']),r['unmatched_count'],js(r['unmatched_examples'])])
    conversion=by['gene_id_convert'];convertrows=[]
    for c in conversion['fields'][2:]:convertrows.append([c['field'],c['nonmissing'],pct(c['nonmissing']/conversion['rows']),c['distinct_nonmissing'],c['missing'],c['max_multiplicity']])
    pairrows=[]
    for r in rel['conversion_pairs']:
        cardinal='多对多（共同出现）' if r['a_multiple_targets'] and r['b_multiple_sources'] else ('多对一' if r['b_multiple_sources'] else ('一对多' if r['a_multiple_targets'] else '一对一'))
        pairrows.append([r['a'],r['b'],r['distinct_pairs'],r['a_multiple_targets'],r['b_multiple_sources'],r['max_targets_per_a'],r['max_sources_per_b'],cardinal])
    pairwise=rel['pairwise'];internal=[r for r in pairwise if r['a_field'] in {'geneID','GeneID','tag_id','chrom_tag_id','ID'} and r['b_field'] in {'geneID','GeneID','tag_id','chrom_tag_id','ID'}]
    network=[r for r in pairwise if r['a_field']=='nodes' and r['b_field']=='nodes']
    ptable=lambda rows:table(['A 文件/字段','B 文件/字段','A 唯一数','B 唯一数','交集','交集/A','交集/B'],[[r['a_file']+'/'+r['a_field'],r['b_file']+'/'+r['b_field'],r['a_distinct'],r['b_distinct'],r['intersection'],pct(r['coverage_a']),pct(r['coverage_b'])] for r in rows])
    relationships_text='''## 如何解释“已验证”

- **已验证（数据层）**：本次全量扫描的字符串相等、非缺失唯一性、集合重合、复合键重复和基数。不是外部数据库约束已创建。
- **来源可对照**：公开 TPIA 页面可解释材料标签或功能用途，但不能证明这批 CSV 的导出版本和自定义内部代码。
- **待确认（生物学语义）**：这些字符串是否为同一 locus、某个 transcript/protein、跨版本对应、正交同源、同一实验样本或实际化学测量。

`#` 是各文件自己的导出序号；`ID/id` 大多是各表行 ID。不同表的 `ID=1` 不代表同一基因。只有主表 `chrom_tag.ID` 与子表明确引用字段之间发现了稳定的数值文本匹配；其生物学引用定义仍需原始表结构/字典。

## ID 的实际形式

| 层级 | 实际形式 | 本批证据 | 限制 |
| --- | --- | --- | --- |
| 主目录内部键 | `1`、`173`、`50552` 等文本 | `chrom_tag.ID` 非空唯一，包含 CSS 与 13 个非 CSS 实体 | 不是 NCBI GeneID，不是生物学基因数 |
| 位点形式编号 | `CSS0000001` 等 | 50,525 条标准形式且对应内部 ID 的补零形式 | 不能用数字补零替代主表查找；其他 13 条无此形式 |
| 转换表 SCZV2 | `CSS0000001.1` 等 | 50,525 个唯一值，去末尾数字后缀的候选值与 CSS 集合吻合 | `.1` 表示异构体还是版本未有字典，不全局剥离后缀 |
| 旧材料/版本编号 | `TEA011977.1`、`CSA026183.1`、`GWHT...`、`LG...` | 保存在转换表不同材料列，允许重复与 NA | 跨材料同一行不是身份等价关系 |
| 外部蛋白编号 | BLAST accession 如 `Q9LIC2` | 外部命中序列记录 | 不是茶树本地 protein/transcript ID，不可与本地数字 ID 直接连接 |
| gene symbol/别名 | 缺少独立统一别名表 | `GeneName` 多是位点编号；`name/productName` 多是描述 | 蛋白描述中 `GN=...` 属命中对象，不能自动注册为本地基因名称 |

序列表的 `Transcript`/`Protein` 是序列字符串，不是转录本/蛋白 ID；本批没有独立的 gene→transcript→protein 多异构体关系表。

## 到主目录的覆盖

覆盖率分母：来源覆盖 = 交集/来源非缺失唯一 ID；主目录覆盖 = 交集/主表全部 50,538 个内部键或名称。该分母含 13 个非 CSS 实体。需要基因覆盖时应另以 50,525 个标准 CSS 位点为分母，不能混用。

'''
    text02=['# 02 跨表关系、ID 重合与映射风险\n\n',intro,common,relationships_text,table(['来源文件','字段','来源唯一 ID','主目录匹配','来源覆盖','全主目录覆盖','未匹配数','例子'],linkrows),'\n',
      '为比较数字引用与 CSS 字符串，补充了经主目录投影的**候选 CSS 集合**。全部 18 文件有 153 对文件级比较，见 [`candidate_css_file_overlap.csv`](audit_final/candidate_css_file_overlap.csv)；这表示可追溯的词法投影，不升级为已确认生物学等价。表达表投影时明确排除 13 条非 CSS 实体。\n\n',
      table(['文件','候选标准 CSS 数','对 50,525 标准位点覆盖'],[[r['file'],r['candidate_css_entities'],pct(r['coverage_of_standard_css'])] for r in follow['candidate_css_file_coverage']]),'\n',
      f'标准 CSS 名称补零规则满足行数：{checks["tpia_chrom_tag_css.csv"]["gene_name_equals_css_padded_id"]}；其他 13 条不能靠补零归一化。转换表候选后缀变换：`{js(rel["conversion_base"])}`。按转换表 id 偶合连接主表的检查：`{js(rel["conversion_id_vs_master"])}`；即使本批数值吻合，转换表 id 仍按行标识符处理，业务映射应经 SCZV2 原标识符证据。\n\n',
      '**732 个非 .1 后缀记录**：.1=49,793、.2=629、.3=83、.4=12、.5=6、.6=2。下面列前 5 条非 .1 示例；全部基础 CSS 编号与本批同 id 的主表名称一致，没有发现基础编号错配。id 仍按源行字段保留，其正式引用定义需字典；不把“全部补 .1”用作归一化规则。\n\n',
      table(['转换表记录号','行 id','SCZV2 原值','同 id 的主表 CSS 基础编号'],[[r['record'],r['conversion_id'],r['SCZV2'],r['id_join_base_name']] for r in follow['conversion_suffix_not_one_records'][:5]]),'\n',
      '## 主要基数与可连接范围\n\n',table(['关系','数据层结果','业务建模与待确认'],[
       ['主目录→序列','50,525 个内部 ID 一对一；13 个非 CSS 实体无序列','可保持源记录引用；是否每基因一个代表转录本待确认'],
       ['主目录→BLASTP','37,448 个 ID，一对多命中；accession 可被多个基因命中','基因—外部蛋白为多对多证据，不是身份外键'],
       ['主目录→domain/PFAM/IPR','39,539 个 ID，一对多区间/方法记录','保留方法、区间和原行；gene+accession 不能唯一'],
       ['主目录→GO/KO','30,257 个 ID，一对多注释','基因与注释项多对多；注释项不是实验事实'],
       ['主目录→TF/TR','2,884 个 tag_id 当前唯一','本批是 0..1；未来可扩展多来源分类，不能据此限制所有未来数据'],
       ['主目录→val','50,538 个 ID，geneID+sampleID 本批唯一','其中 50,525 标准位点×50，另 13 非 CSS×84；数值测量实体分层'],
       ['主目录名称→网络端点','每一网络两端独立核对，原有序边一般不对称','每数据集内基因对多对多；无向生物学相关不等于输出边对称'],
       ['转换表 SCZV2→其他列','每个 SCZV2 一行，每目标列最多一个原值；目标可由多个 SCZV2 共用','正向 0..1，反向 0..N；其他两列共同出现可呈多对多，不自动推导直接正交映射'],
      ]),'\n## 核心内部引用表的两两重合\n\n',ptable(internal),'\n## 10 张网络的两两节点重合\n\n',ptable(network),'\n',
      '完整比较包括每张网络 tag1/tag2、节点并集、内部 ID 表以及候选 SCZV2 基础编号，详见 [`id_overlap.csv`](audit_final/id_overlap.csv)。不同命名空间不直接比较数字 ID 与 CSS 字符串；跨层查询需经过主目录的记录级映射。\n\n',
      '## 转换表的缺失、覆盖与歧义\n\n',table(['列命名空间','有值行数','覆盖 SCZV2 行','不同原始 ID','候选缺失','单 ID 最大出现次数'],convertrows),'\n',
      '以下是**同一导出行共同出现**的基数统计，不等于作者提供了该两列之间的直接正交关系。A 多目标/B 多来源按非缺失唯一组合计算，重复值不先去除原始行。\n\n',table(['A','B','唯一组合','A 有多个 B 的 ID 数','B 有多个 A 的 ID 数','A 最大目标数','B 最大来源数','共同出现基数'],pairrows),'\n',
      '## 未覆盖、异常与需要来源确认的关系\n\n',
      '1. 13 个非 CSS 实体与所有观测记录均能词法引用主目录，但这不表示基因引用完整。其内部 ID 是 50552..50564；50526..50551 未出现在目录，不补造记录。\n',
      '2. 某个标准基因缺少 GO、KO、BLAST、domain、TF 或某个网络记录，仅说明该导出没有记录；不能解释为没有相应生物功能或相关性。各表覆盖不同，综合查询须用主目录起始的 LEFT JOIN/分表聚合，避免连乘扩张和 INNER JOIN 丢记录。\n',
      '3. 标准基因 val 使用 101..150，非 CSS 实体 val 使用 151..234，样本编号交集为空。未发现能把二者配成同一生物样本的映射表。\n',
      '4. 转换表其他材料列的 ID 无对应材料主表，不能当作“外键孤儿错误”；本地仅有它们的标识符映射文本，缺少位置、序列和物种/版本的完整主数据。\n',
      '5. 网络文件的 ca_public、naci、shadding、meja、peg 等是原始数据集代码；处理名称、浓度、时长、样本数量、相关系数算法及是否 top-k/阈值筛选待确认。尤其不静默把 naci 改写成 NaCl。\n',
      '6. Genome=3、CSS/SCZV2 的组装与注释版本、`.1` 后缀语义、内部 geneID 的实体层级、BLAST s/m 含义、GO 证据来源与转换算法必须查阅原始导出 DDL/数据字典。\n\n',
      '## 公开文档能够确认到哪里\n\n',
      'TPIA [Gene ID Convert 页面](https://tpia.teaplants.cn/geneIdConvert.html)确有对应材料选择；[共线性页面](https://tpia.teaplants.cn/linear.html)列出了 SCZV1/V2、Biyun、HD、TGY、LJ43、YK10、DASZ、COL、CCH 的公开物种/材料名称。其中 DASZ 对应 Camellia taliensis，COL 对应 C. oleifera，CCH 对应 C. chekiangoleosa，不能把所有转换列当成同一物种的简单别名。该公开命名并不证明本批内部 Genome=3 的含义、映射算法或具体组装 accession。\n\n',
      '公开页面查询日期 2026-11-03；未向站点提交本地数据或单个本地 ID。官方 Manual.pdf 的全文抓取超时，仅获得搜索引擎索引片段；其中存在 Pearson 工具的展示，不能据此认定本导出 personR/personP 已确认。原论文全文页访问受限，本审计未用不可见全文来确认字段。\n']
    write('02_data_relationships.md',''.join(text02))
    # 03：逐场景可实现性。
    qa=[
      ['基因基本信息','CSS0000001 的位置、链方向和原始产物描述是什么？','chrom_tag','ID/GeneName/Genome/chrom/start/end/strand/productName','可直接查询；版本标签待确认','SQL + ID 规则','坐标制、组装/注释版本、产物来源'],
      ['功能注释','CSS0000173 有哪些已有注释？','chrom_tag + gene_annotation；可加 domain/BLAST','GeneID/AnnoID/AnnoName/AnnoType','可直接返回记录','SQL；名称可全文检索','GO 证据代码、注释日期/方法、实验文献'],
      ['BLASTP 命中','列出 CSS0000001 已存的蛋白比对记录和 accession。','chrom_tag + annoblastpdetail','geneID/accession/name/sLen/sStart/sEnd/mStart/mLen/mEnd/mIdentity/sIdentity/pValue','可查询既有命中，不能运行新 BLAST','SQL + accession/名称检索','数据库版本、算法参数、s/m 指标定义；不能确证正交或功能'],
      ['PFAM / IPR','某基因有哪些 PFAM 和 IPR 区间？','chrom_tag + annopfamdetail','geneID/accession/name/mStart/mEnd/eValue/iType/mothod','可直接按 iType/方法分类','SQL；名称全文检索','版本、阈值和不同方法的数值解释'],
      ['GO、KEGG','有哪些 GO term 和 KO？哪些基因有 K00001 注释？','chrom_tag + gene_annotation','GeneID/AnnoID/AnnoName/AnnoType','GO/KO 可；完整通路解释/富集不可直接承诺','SQL + 规则；文献解释需另建 RAG 语料','GO ontology、证据代码、KO→通路映射/版本、统计背景集'],
      ['转录因子','查询 tf_type=TF 且 tf_name=TCP 的记录。','chrom_tag + tfactor','tag_id/tf_type/tf_name/tf_desc','可按原始枚举查询；TR 等单列','SQL','分类字典、ARR-B/p15 归类、无 TF→target 调控边'],
      ['基因原始观测值','CSS0000001 在 sampleID=101 的原始 val 是多少？','chrom_tag + gene_expro','ID/GeneName/geneID/sampleID/val','可直接返回 raw val；不称 TPM/FPKM','SQL，精确数值输出','50 个编号的样本字典、单位、归一化、重复与实验设计'],
      ['多组织表达比较','这个基因在叶和根哪个表达高？','gene_expro + 尚缺 sample metadata','sampleID/val；还需 tissue/replicate/condition/unit','当前不能赋予组织含义；仅可比较指定编号原值','补全元数据后 SQL + 统计；RAG 只解释','组织映射、同实验可比性、单位/批次/对照'],
      ['胁迫相关基因','在 ectropis 数据集，CSS0000008 有哪些已有相关边？','ectropis_co 或指定其他 *_co + chrom_tag','tag1/tag2/personR/personP；dataset code','可查原始网络；不能称胁迫上调/抗性基因','SQL / 图邻接规则，保留数据集隔离','处理定义/时间/浓度、样本与统计设计、完整表达和差异检验'],
      ['ID 转换','将 SCZV2 的 CSS0000001.1 按已有表映射到 LJ43。','gene_id_convert；候选 base 另接 chrom_tag','SCZV2/LJ43/其他列/id','可回显已有映射；版本/实体等价待确认','SQL + 命名空间规则，反向返回多个值','算法/identity/coverage/synteny、目标版本、后缀含义'],
      ['功能综合分析','综合数据库已有 GO/KO、结构域、比对和原值证据描述该基因。','chrom_tag + annotation/domain/BLAST/TF/sequence/val','版本化 ID + 各表原字段 + 来源行号','可以做可追溯的记录汇总；不能声称已验证功能','SQL 聚合 + 全文/语义检索候选 + 受证据约束 RAG','文献/实验证据与专家金标；多源注释不必独立'],
      ['共表达邻居','在 eight_tissue 数据集按 |personR| 排出已有邻居。','指定 *_co + chrom_tag','tag1/tag2/personR/personP','可查已导出的 10 邻居；不能当全网排名','SQL 双端查询；去重只在派生视图并保留原行','筛选算法、是否对称、相关检验/校正'],
      ['序列提取','提取 CSS0000001 的 CDS 或 Protein。','chrom_tag + chrom_sequence','ID/chrom_tag_id/CDS/Protein','可直接取原字符串','SQL / 文件流；长序列以附件/结构化结果返回','Transcript 与 CDS 重复，缺少独立异构体、翻译与参考版本验证'],
      ['非 CSS 命名实体值','查询 Theanine 在 sampleID=151 的原始 val。','chrom_tag + gene_expro','ID/GeneName/geneID/sampleID/val','可原样回显；实体类型和单位待确认','SQL + 实体类型规则','物质/性状正式字典、单位/方法、84 编号与基因样本配对'],
      ['基因—表型/多模态','哪个基因决定茶氨酸含量？能结合叶片图像解释吗？','仅有疑似化学性状命名的 13 组数值','没有已确认配对样本、表型/图像或因果证据','当前不能直接实现该生物学问题或多模态问答','先补配对数据与统计证据，再讨论混合查询','无图像/音视频；样本 ID 不重合，缺表型本体/测量与关联实验'],
    ]
    write('03_qa_capabilities.md','# 03 问答能力与边界\n\n'+intro+common+table(['场景','可复制问题示例','所需 CSV（省略 tpia_/_css.csv）','所需字段','目前可直接实现程度','适合技术','缺口与限制'],qa)+'''
## 技术选择依据

精确 ID、数值、区间、排序、计数和关联优先采用参数化 SQL/规则查询。GO/KO/PFAM/accession 精确匹配使用结构化索引；英文名称用全文检索。中文自由问法和英文注释之间的语义检索可作为研究变量，当前没有中文同义词或问答金标，不假定向量检索必然更好。

本批主体是短注释、区间与数值记录，不是自然语言论文库。RAG 的首要作用是把结构化查询结果作为受约束证据输入回答层，并携带文件、记录号和原始字段；大量序列、整张数值表和网络边不宜直接拼成大文本向量化。精确数据由查询层输出，回答层不得自行计算、补造版本或扩展生物学因果。

综合查询按证据类型分开聚合，避免 BLAST×PFAM×GO×样本的笛卡尔乘积。应有结果条数、分页和返回范围说明，不能用前 k 条冒充全量记录。无记录返回“本导出未检索到”，不解释为生物学上不存在。

## 必须执行的回答规则（下一阶段设计要求，未实现）

1. 给出命名空间/版本、来源 CSV、记录号、原始标识符与检索条件。
2. 区分记录事实、已有计算注释、助手推断和需要实验验证的假设；不得把同源比对/结构域/共享 GO 写成实验证明。
3. sampleID 不翻译成组织/处理；val 不加未经确认的单位；不按编号分组推断处理上调/下调。
4. 网络中相关性不等于调控方向、互作、抗性功能或因果，10 邻居导出不用于完整网络统计。
5. 13 个非 CSS 实体不会默认进入“全部茶树基因”检索；确认实体字典前按原始命名实体展示。
6. 对跨版本转换输出所有候选与来源，提示映射性质待确认；找不到记录和无法识别格式分别返回。

## 首批确定性验收题建议

先由专家从本地 CSV 核准 30–50 个小型验收问题，覆盖：完整 CSS ID、内部键、转换后缀、反向多候选、GO/KO 区分、IPR/PFAM 区分、13 个非 CSS 实体、缺失产物描述、原始零值、无记录、网络方向与“没有样本元数据时不能给组织名”。这只是工程验收集合，不作为论文独立测试集。后续自然语言题集按基因/映射组、问题模板和证据来源做隔离，保留专家盲测。
''')
    # 04：只做结构设计，FK 语义边界必须显式。
    write('04_database_design.md','# 04 PostgreSQL 初步数据库设计（仅文档）\n\n'+intro+common+'''
## 设计结论与依据

建议以 PostgreSQL 的版本化实体目录、证据表、原始观测表和来源追踪为核心。10 个网络文件合为带 dataset_id 的边表；同结构 GO/KO 保留一个注释记录表；PFAM/IPR 保留一个 domain_hit 表。初期无需图数据库、完整本体库、向量扩展或一列一表的过度拆分。

最关键的调整是：**主目录 50,538 行不是 50,538 个基因**。应分离来源实体记录与生物学 gene_locus；13 个非 CSS 命名实体及其 val 不写入基因表达表。`Genome=3` 先登记为未决的来源上下文；不能直接等同于某个已确认参考基因组外键。

本批所有 ID 使用 TEXT，内部代理主键可使用 BIGINT。`val` 同时保留原始字符串与 NUMERIC；不同域的序列及字符串不强制转换。BLAST/PFAM 极小值保留原文并使用 NUMERIC（不设过小 scale）或可选派生 −log10 指标，字面 0 不能计算无限置信度。

每个 typed 证据表的公共解析字段包括 `candidate_entity_id`、`resolved_gene_id`、`semantic_status` 与 `resolution_id`；下表仅列各表重点字段。`semantic_status` 取 pending/confirmed/rejected，默认 pending，和实体目录本身的类别确认状态分别保存。

## 核心表、字段与约束

| 核心表 | 核心字段（类型建议） | 主键/唯一约束 | 外键与用途 |
| --- | --- | --- | --- |
| import_batch | batch_id BIGINT, imported_at TIMESTAMPTZ, audit_version TEXT, status TEXT | PK batch_id | 一次导入/审计的批次；不是 CSV mtime 所代表的数据发布日期 |
| source_file | source_file_id BIGINT, batch_id, relative_path TEXT, sha256 TEXT, bytes BIGINT, encoding TEXT, delimiter TEXT, rows BIGINT, source_url TEXT NULL, retrieved_at NULL, release_raw TEXT NULL | PK；UNIQUE(batch_id,relative_path) | FK batch_id；原文件名、哈希、读取方式和导入版本；来源 URL 不凭文件名编造 |
| source_record | record_id BIGINT, source_file_id, record_no BIGINT, source_row_id TEXT, export_no_raw TEXT, raw_fields JSONB, row_sha256 TEXT | PK；UNIQUE(source_file_id,record_no) | FK source_file_id；短记录保留原字段，序列可转引用保持可恢复；用于一条证据追到原行 |
| reference_context | context_id BIGINT, source_genome_code TEXT, namespace TEXT, species_text TEXT NULL, taxon_id TEXT NULL, cultivar TEXT NULL, assembly_accession TEXT NULL, assembly_version TEXT NULL, annotation_release TEXT NULL, confirmation_status TEXT | PK；只对已确认完整版本定义适用 UNIQUE | 先建本批 CSS/Genome=3 的 provisional 上下文；物种/版本无资料保持 NULL，不与十套材料混合 |
| entity_record | entity_id BIGINT, source_file_id, source_internal_id TEXT, name_raw TEXT, source_context_id, entity_kind TEXT, classification_status TEXT, source_record_id | PK；UNIQUE(source_file_id,source_internal_id) | FK source/context/record；装载主目录全部 50,538 行；kind 初始 candidate_gene / candidate_chemical_trait / unknown |
| gene_locus | gene_id BIGINT, reference_context_id, locus_id TEXT, chrom_raw TEXT, start_raw TEXT, end_raw TEXT, start_value BIGINT NULL, end_value BIGINT NULL, strand_raw TEXT, product_name_raw TEXT, aa_count_raw TEXT, exon_count_raw TEXT, exon_starts_raw TEXT, exon_ends_raw TEXT, UTR 原始四列 TEXT, gtype_raw TEXT, entity_id | PK；UNIQUE(context_id,locus_id)；entity_id 唯一 | 仅在实体/参考语义确认后由 entity_record 解析；目前文档中的候选基因为 50,525 个，不实施迁移 |
| entity_resolution | resolution_id BIGINT, record_id, source_field TEXT, source_identifier_raw TEXT, candidate_entity_id NULL, lexical_status TEXT, semantic_status TEXT, method TEXT, confirmed_by TEXT NULL, evidence TEXT | PK；UNIQUE(record_id,source_field,source_identifier_raw) | FK 仅到原记录/实体候选；词法匹配不自动使 semantic_status=confirmed；gene_id 等生物学 FK 仍可为空 |
| gene_sequence_record | seq_record_id BIGINT, record_id, source_chrom_tag_id TEXT, candidate_entity_id NULL, resolved_gene_id NULL, Protein/Transcript/Gene/Upstream/Downstream/CDS TEXT, 各序列 hash/length | PK；UNIQUE(record_id) | source_record FK；只有确认后 resolved_gene_id FK 到 gene_locus；本批每引用一个序列包，未来异构体另扩展而非预先虚构 ID |
| annotation_record | annotation_record_id, record_id, source_gene_id TEXT, candidate_entity_id NULL, resolved_gene_id NULL, anno_type_raw TEXT, anno_id_raw TEXT, anno_name_raw TEXT, evidence_code TEXT NULL, source_release TEXT NULL | PK；UNIQUE(record_id) | source_record FK；不先强制 UNIQUE(gene,term)，保留多来源与重复证据；可建派生去重视图 |
| blast_hit | hit_id, record_id, geneID_raw, candidate_entity_id NULL, resolved_gene_id NULL, accession TEXT, name_raw TEXT, sLen/sStart/sEnd/mStart/mLen/mEnd 原始 TEXT 与可空整数派生列, mIdentity/sIdentity/pValue 原始 TEXT 与 NUMERIC | PK；UNIQUE(record_id) | 不给 accession 建本地 gene FK；多个 gene 命中同一外部蛋白；源数据库/参数/版本补齐后再加业务约束 |
| domain_hit | hit_id, record_id, geneID_raw, candidate_entity_id NULL, resolved_gene_id NULL, accession/name/mStart/mEnd/eValue/iType/mothod 原始字段，坐标/分值派生列 | PK；UNIQUE(record_id) | 同 accession/区间可多方法且有 3 组业务重复；不建会拒绝原数据的 gene+accession 或区间唯一约束 |
| factor_assignment | assignment_id, record_id, tag_id_raw, candidate_entity_id NULL, resolved_gene_id NULL, tf_type_raw TEXT, tf_name_raw TEXT, tf_desc_raw TEXT, validation_status | PK；UNIQUE(record_id) | TF/TR/ARR-B/p15 均原样接收；不约束类别只能 TF/TR，不创建不存在的 target 调控表 |
| observation_dataset | observation_dataset_id, source_file_id, dataset_code_raw TEXT, measurement_kind TEXT, unit TEXT NULL, normalization TEXT NULL, study_id TEXT NULL, confirmation_status | PK | 可先按原始观测分区标记 candidate_expression / candidate_chemical_trait；字段分类不证明单位 |
| sample_record | sample_record_id, observation_dataset_id, sample_id_raw TEXT, metadata JSONB, tissue/condition/timepoint/replicate/unit 可空, mapping_status | PK；UNIQUE(observation_dataset_id,sample_id_raw) | 不把 sampleID 设成全库样本主键；不同导出、研究、测量可复用数字 |
| raw_observation | observation_id, record_id, observation_dataset_id, source_entity_id_raw TEXT, candidate_entity_id NULL, resolved_gene_id NULL, sample_record_id, value_raw TEXT, value_numeric NUMERIC NULL, measurement_kind, unit NULL, normalization NULL | PK；UNIQUE(record_id) | 保留全部 2,527,342 行；本批 (dataset,source_entity_id_raw,sample) 唯一可作导入后校验，未确认重复类型前不设跨批全局唯一 |
| network_dataset | dataset_id, source_file_id, dataset_code_raw, method_raw NULL, selection_rule NULL, sample_set_id NULL, threshold_metadata JSONB, confirmation_status | PK；UNIQUE(source_file_id) | 保存 10 个原始代码，包括 naci/shadding；不自行改名或假定 top10 算法 |
| network_edge | edge_id, record_id, dataset_id, tag1_raw TEXT, tag2_raw TEXT, entity1_candidate_id NULL, entity2_candidate_id NULL, personR_raw TEXT, personP_raw TEXT, r NUMERIC, p NUMERIC | PK；UNIQUE(record_id) | 本批可校验 UNIQUE(dataset_id,tag1_raw,tag2_raw)；保留有序边和双端索引，不跨数据集混边 |
| id_mapping_row | mapping_row_id, record_id, source_conversion_id TEXT, SCZV2/Biyun/DASZ/HD/LJ43/SCZV1/TGY/YK10/COL/CCH 原始字段 | PK；UNIQUE(record_id) | 一行完整保留映射上下文，不假定为正交群或身份等价类 |
| identifier_record | identifier_id, reference_context_id, namespace_code TEXT, identifier_raw TEXT, entity_level TEXT NULL, candidate_entity_id NULL, semantic_status TEXT | PK；UNIQUE(reference_context_id,namespace_code,identifier_raw) | 标识符实体不要求目标材料的 gene_locus 已导入；`.1` 原样保留 |
| mapping_assertion | assertion_id, mapping_row_id, source_identifier_id, target_identifier_id, mapping_type_raw NULL, confidence_raw NULL, semantic_status, transform_rule NULL | PK；UNIQUE(mapping_row_id,source_identifier_id,target_identifier_id) | 只存原行 SCZV2→目标列的来源断言；其他材料间共现不自动转直接映射；不传递闭包、不强制一对一 |

source_record 与 typed 证据表可形成一对一物理引用，实体到多条证据是一对多。基因—注释项、基因—外部命中蛋白、网络端点、跨材料标识符为多对多，应保留中间证据记录。首期不另建每个描述词/外显子/UTR 的维表；保留原列表，若确有区间检索需求再解析派生表。

## 关系示意

```mermaid
erDiagram
  import_batch ||--o{ source_file : contains
  source_file ||--o{ source_record : records
  source_file ||--o{ entity_record : directory_snapshot
  entity_record ||--o{ entity_resolution : candidate_reference
  source_record ||--o{ entity_resolution : lexical_evidence
  reference_context ||--o{ gene_locus : version_scope
  entity_record ||--o| gene_locus : after_confirmation
  source_record ||--o| annotation_record : typed_evidence
  source_record ||--o| blast_hit : typed_evidence
  source_record ||--o| domain_hit : typed_evidence
  source_record ||--o| raw_observation : typed_value
  sample_record ||--o{ raw_observation : measured_in
  network_dataset ||--o{ network_edge : original_ordered_edges
  id_mapping_row ||--o{ mapping_assertion : source_assertions
  identifier_record ||--o{ mapping_assertion : source_or_target
```

图中到 gene_locus 的解析表示**确认后**可建立的关系，当前审计不执行。跨文件词法命中存 candidate_entity_id 及 resolution 证据，未经确认的生物学 ID 不写成强制 gene FK。13 个非 CSS 命名实体仅进入 entity_record/raw_observation，若导师确认物质/性状类别，可派生 chemical_entity/trait_record；不预建已确认基因—性状关联。

## 约束与索引建议

- 所有 typed 记录以 source_record 为来源锚，导入键含文件/批次；相同源文件重复导入需先比较 sha256，再决定复用还是新快照。
- source_file 的 UNIQUE(batch_id,relative_path)、source_record 的 UNIQUE(source_file_id,record_no) 保证可追踪；同一文件 hash 可跨批次复用，不用全局路径当不可变来源。
- gene_locus 的 UNIQUE(reference_context_id,locus_id) 只有已确认版本才有生物学含义；未知组装/注释不以 NULL 误拼成同一版本。
- 注释索引 `(candidate_entity_id,anno_type_raw)`、`(anno_type_raw,anno_id_raw)`；映射索引 `(namespace_code,identifier_raw)` 以及 assertion 的 source/target 双向索引。
- BLAST/domain 索引 `(candidate_entity_id)`、`(accession)`、domain 的 `(iType,accession)`。区间索引在坐标制确认后考虑 range/GiST，首期普通 B-tree 足够。
- 观测索引 `(observation_dataset_id,source_entity_id_raw,sample_record_id)` 与 `(observation_dataset_id,sample_record_id,value_numeric)`；数量约 253 万，本批无需预先复杂分区。未证明有收益前不为每个数值列建索引。
- 网络索引 `(dataset_id,tag1_raw)` 和 `(dataset_id,tag2_raw)`，需要邻居排序时再评估 `(dataset_id,tag1_raw,r)`。不把排序字段 r 的绝对值索引作为未经性能验证的默认。
- 对英文 productName/AnnoName/name 可设计 `tsvector`+GIN，模糊英文拼写可选 `pg_trgm`。CSS/GO/KO/家族编号精确查询保留 B-tree；向量检索是后续可比较变量，当前不安装扩展。
- 长序列 TEXT 可独立表存储，避免每次档案检索搬运序列；原始文件 path/hash/record 锚保证恢复，不在 JSONB 再重复存储所有长序列。JSONB 中“key 不存在”和“空字符串/NA 原值”不得混淆。
- 数值解析失败进入质量状态，原字段不丢弃。r/p 的范围约束与 val 的非负约束只在确认具体字段语义后应用到 typed 派生层；原始层接受所有源字符串。`eValue<=1` 不应是 domain_hit 的通用 CHECK。
- 当前组合唯一性是观测结果；跨批数据、重复实验和重复注释应保留，不能使用 UPSERT 的静默覆盖策略。业务去重只能做可追溯视图。

## 映射状态的约束原则（示例，未执行）

```sql
-- 示例用于说明设计边界，不是本阶段的迁移脚本。
-- 在 typed 证据表内：词法候选可有值，确认前 biological FK 必须为空。
CHECK (resolved_gene_id IS NULL OR semantic_status = 'confirmed')
```

物理 FK 可验证“引用的源记录/候选实体存在”，不能证明“生物学映射成立”。主目录数据层引用即使 100% 命中，也只产生 lexical_status='matched'；semantic_status 由 DDL/字典、版本和专家确认。

## 设计验收与当前状态

下一阶段导入应验证：源文件 hash、行数/列宽、候选缺失、主键冲突、引用覆盖、13 类别隔离、两个观测块完整度、网络方向/数据集隔离、PFAM/IPR 多方法保留、映射逆向多候选、样本和版本未决状态。先完成小型只读查询验收，再做自然语言层。

**本阶段仅生成设计，没有 PostgreSQL 实例、迁移、建表、索引、服务或 RAG 实现；上述 SQL 及性能均未运行验证。** PostgreSQL 约束行为与索引能力参考官方 [Constraints](https://www.postgresql.org/docs/current/ddl-constraints.html)、[pg_trgm](https://www.postgresql.org/docs/current/pgtrgm.html)（2026-11-03 查阅）；实际部署版本需要下一阶段另行选定。
''')
    # 05：假设、基线和独立评估，不声称创新。
    write('05_research_opportunities.md','# 05 科研方向、数据缺口与初步实验设计\n\n'+intro+common+f'''
## 可行性判断

当前数据最适合研究“版本/实体约束下，如何正确检索并忠实组织已有证据”。B 与 C 可以构成同一主线：检索正确性与回答证据忠实性；A 是明确可验证的前置问题。D 的原始记录联合查询可行，但组织/处理解释受样本字典限制。E 仅见 13 个疑似化学成分/汇总性状命名实体的 {groups['nonstandard_named']['rows']} 条数值，缺样本配对和多模态资源，目前不能形成多模态课题。

这些是可检验的候选研究问题，**尚未进行系统相关工作检索，不能称作论文创新点**。本次公开查阅只用于核对 TPIA 命名背景和 PostgreSQL 行为；未训练模型、未跑实验、未调用付费 API，也没有效果数字。

## A. 茶树基因实体识别与 ID 归一化

- **现有支持**：50,525 个 CSS 位点、SCZV2 带后缀标识符、其他 9 个材料/版本列，反向多候选及 13 个非 CSS 实体，适合测试命名空间混淆、后缀处理和歧义解析。没有自然语言实体标注语料、统一 gene symbol/中文别名表，不能仅靠规则编号识别宣称完成生物医学 NER。
- **需补充**：实体层级/版本字典、可信别名与专家标注问句、混合材料示例、真实拼写变体、歧义和无映射负例、目标材料完整主数据。`.1` 是否转录本需原始定义。
- **技术问题/假设**：在同一题集下，显式命名空间与候选集合输出是否降低跨版本误配及错误单一答案率？预期减少误配，尚待验证。
- **基线**：精确字典；正则+精确字典；模糊字符串；带上下文的实体链接模型；在相同候选生成器下仅增加命名空间约束。不能把“盲目去后缀”作为唯一弱基线。
- **评估**：mention span P/R/F1；链接 Accuracy、候选 Recall@k、集合答案 F1、跨版本错配率、歧义保留率、拒绝无证据映射准确率。按映射连通组/源行划分，防同一个反向重复标识符进入训练和测试。
- **难点**：别名稀缺、跨材料不是身份等价、多对一、实体/基因混装、真实问句稀少。规则数据很容易但不代表自然语言能力。
- **硕士适配**：适合作为基础模块和子问题；若独立成论文须补真实歧义语料和跨库/跨版本泛化评估，不能以正则解析本身作为创新。

## B. 面向基因功能问答的混合检索

- **现有支持**：135,198 条 GO/KO、168,837 条蛋白命中、302,525 条 PFAM/IPR 和 50,525 个标准位点的描述/序列。文本以英文短注释为主，数值和 ID 必须走结构化路线。
- **需补充**：中文真实科研问题、问题→结果集合/证据行金标、术语同义词、任务复杂度标签；如做文献 RAG，再补许可明确的论文/摘要与证据定位，不把外部语料混称为现有数据。
- **问题/假设**：固定语料和生成模型后，SQL 约束+词法+语义检索是否提高跨语言/多条件问题的证据召回与最终正确性，同时控制延迟？
- **基线**：SQL/规则（对可解析结构化题）；BM25/数据库全文检索；dense；词法+dense 融合；相同融合器增加实体/版本过滤；再单独比较是否加入 reranker。对精确 ID 题不能只与 dense 的弱基线比较。
- **评估**：Evidence Recall@5/10、MRR/nDCG（需要等级相关性金标）、结果集合 F1、SQL/查询执行正确率、端到端答案正确率、P50/P95 延迟与每题资源消耗。精确 ID、中文别名、多条件、不可答分层报告。
- **难点**：短注释重复、中文—英文匹配、ID 被 embedding 弱化、结构化过滤不可丢失、检索命中不一定能支持回答。完整 ID 查询与自由文本查询应分别评估再汇总。
- **硕士适配**：适合 AI 应用/检索方向，需设计公平基线和独立金标，不能只演示“有向量库”。

## C. 多源注释的证据融合与可信问答

- **现有支持**：同一 CSS 目录可关联 GO/KO、PFAM/IPR、多方法 domain、BLAST 和产物描述；当前属于计算注释与来源记录，缺少证据代码、PMID/DOI、实验条件和人工功能结论。
- **需补充**：来源版本、annotation 方法、证据等级/实验标签、专家审核的 claim→evidence 对齐、可答/不可答金标，以及真实冲突或信息不足案例。不能把不同算法产物默认为独立证据，IPR 的 mothod=Pfam 与 pfam 原记录可能相关。
- **问题/假设**：固定检索结果和模型，明确来源/证据类型/版本并要求逐结论引用，是否降低不受支持陈述和关联升级为因果的错误？是否因拒答过多降低覆盖？
- **基线**：固定证据的直接模板汇总；相同证据 plain RAG；仅增加来源标签；仅增加逐结论证据约束；仅增加拒答校准；不同证据组合的单源移除消融。无检索 LLM 仅作为诊断，不作为唯一主基线。
- **评估**：事实正确率、claim-level 支持率、引用精确率/覆盖率、版本错配率、过度结论率、回答覆盖率与 selective risk/coverage、适当拒答率。引用来自真实行不代表该行支持结论；至少两位标注者独立复核并报告一致性/争议处理。
- **难点**：证据依赖、缺方法元数据、专家成本、合成冲突不代表真实冲突、模型评审的偏差。合成删除/互换证据只作受控鲁棒性测试，必须与真实专家题分开。
- **硕士适配**：适合作为主线候选，既有 AI 方法问题也有领域价值；研究结论限于“忠实回答已有证据”，不能声称发现/验证新基因功能。

## D. 表达、注释与同源证据联合查询

- **现有支持**：标准 CSS 原始观测块完整，{num(groups['standard_css']['rows'])} 条、50 个编号，可与注释/同源记录按内部引用连接。10 张网络另有 {num(co_rows)} 条已有邻居记录，不能用其替代原始表达矩阵和全网络。
- **需补充**：sampleID 元数据、单位、归一化、重复、实验批次、处理与对照、时间/剂量、数值来源与差异表达分析方法；网络样本集合、相关算法/筛选/多重校正规则。
- **问题/假设**：固定结构化数据，显式查询计划与单位/样本校验是否改善跨证据组合查询的集合正确率并减少不合法比较？先评估“查对了没有”，再评估生物学解释。
- **基线**：手写模板 SQL；受约束语义解析→SQL；同模型无约束 text-to-SQL；在相同解析器上仅加元数据合法性校验；纯文本化表格 RAG 作为补充诊断。
- **评估**：执行结果集合准确率、数值正确率、条件/方向/排序正确率、样本/单位误用率、拒答正确率、查询成本与任务完成时间；禁止仅比 SQL 字符串完全一致。
- **难点**：缺样本定义、数值可比性、跨实验混用、一对多连接导致重复计数、样本级泄漏、LLM 自行算术。差异分析必须另有设计，不能从 val 的大小直接推抗性。
- **硕士适配**：工程和数据系统子课题可行；若作为生物学表达研究主线，先补元数据与真实研究问题，当前不足。

## E. 基因—性状关联与多模态问答

- **真实数据边界**：确有 GA/EGCG/Theanine/Caffeine 等 13 个非 CSS 名称，每个 84 个原始值；不能声称“完全没有化学性状线索”。但实体类别/单位未确认，且其 sampleID=151..234，与基因观测 101..150 的交集为零。没有图像、音频、视频或带标签表型文件；序列+注释+数值是多类型数据，不自动等同于经验证的图像多模态任务。
- **需补充**：物质/性状正式字典、测量单位/技术、同一样本或材料的合法配对、组织/处理/生物重复、基因—性状关联结果；若做视觉再补图像、采集条件、版权与标注。不能依赖编号顺序配样本。
- **技术问题**：确认配对后才可研究实体/样本约束的关联证据检索；图像融合等问题目前无数据基础。基因—化学成分统计关联还需控制批次/处理混杂，不能视为调控或因果。
- **基线/评估（条件方案）**：仅结构化性状查询→样本配对 SQL/元数据基线→文本检索融合；若未来确有图像，设置同样划分的单模态/融合基线。评价配对正确率、证据检索、集合/数值答案和泄漏风险；生物学关联需独立验证。当前不执行，也不预报性能。
- **硕士适配**：作为后续数据合作方向；本批不能独立支撑多模态论文，先确认 13 类实体和样本映射是否存在。

## 统一实验矩阵（规划，未运行）

固定项目快照、文件哈希、金标版本、问题集划分、检索 top-k、上下文预算、生成模型版本、temperature=0 或固定采样种子。每组主比较只改变一个因素，其他配置完全一致；不把“增加约束+换模型+换题集”合成一次消融。

| Run | 固定部分 | 唯一改变因素 | 比较对象 | 预期方向（假设） | 主指标 |
| --- | --- | --- | --- | --- | --- |
| A0/A1 | 相同候选字典、题集和模糊匹配 | 命名空间约束关闭/开启 | A1−A0 | 版本错配降低，覆盖可能降低 | 链接集合 F1 / 错配率 |
| B0/B1/B2 | 相同语料、过滤器、k、无 reranker | BM25 / dense / 固定权重融合 | 三种检索器 | 融合改善语义召回，不保证精确题优势 | Recall@10 / nDCG |
| B2/B3 | 同一个融合器、语料、预算 | 实体/版本约束关闭/开启 | B3−B2 | 错实体降低 | 证据召回/结果 F1 |
| B3/B4 | 相同过滤与候选集合 | reranker 关闭/开启 | B4−B3 | 排序可能改善但延迟增加 | MRR / P95 延迟 |
| C0/C1 | 冻结相同检索证据与生成模型 | 来源类型标签关闭/开启 | C1−C0 | 过度结论减少 | claim 支持率 |
| C1/C2 | 相同证据、模型和来源标签 | 逐 claim 引用约束关闭/开启 | C2−C1 | 引用支持提升 | 引用精确率/覆盖率 |
| C2/C3 | 相同生成器与上下文 | 无证据拒答校准关闭/开启 | C3−C2 | 风险降低但覆盖可能降低 | risk-coverage / 合理拒答 |
| C3−source | 固定其余来源、模型和检索预算 | 每次只移除 GO/KO、domain、BLAST 中一类 | 3 次单源移除 | 检验各来源必要性而非预设加权 | 各题型支持率/正确率 |
| D0/D1 | 同解析器、数据和提示预算 | 样本/单位合法性检查关闭/开启 | D1−D0 | 非法比较减少 | 执行正确率/单位误用 |

A 共 2 配置，B 共 5 配置，C 共 4+3=7 配置，D 共 2 配置，总计 16 个规划配置；各方向是否共用题集需按任务分别定义，不跨任务混算总分。先做 A/B/C 小规模预实验，D 等样本字典到位。题集规模建议先 30–50 条验收题，再建设约 200–400 条分层专家题；这些是待讨论的计划值，不是已有数据量或已完成标注。

## 划分、公平比较与分析计划

1. 金标先按基因/映射连通组分 train/dev/test；另按问题模板隔离一套泛化测试，防同义改写泄漏。反向重复目标 ID 不能横跨开发/测试；专家最终盲测问题不参与调参。
2. 检索任务固定候选语料快照；事实答案可以从全语料检索，但不能把测试问题或专家答案嵌入语料。声称新基因泛化时须另外按实体/家族隔离，避免把同一重复注释视为独立样本。
3. 共用数据过滤、token/候选预算、模型、硬件；报告超时/失败/拒答，不只剔除失败题后统计成功率。端到端失败计错，合理不可答作为独立类别。
4. 预先指定主指标（C 建议 claim 支持率与答案覆盖率联合），用按问题/实体组配对 bootstrap 的 95% CI；固定重采样种子 20261103。若调用非确定性本地模型，可做 3 个预先固定种子，报告运行间波动，不挑最好一次。
5. 绘制题型分层检索/答案表、引用支持与覆盖曲线、延迟分布；由两位审核者按手册盲审争议案例，报告一致性。多来源一致不直接合成“实验置信度”。

## 资源估算与执行边界

本阶段花费：远程模型请求 0，付费生成 0，训练/推理实验 0；审计使用标准库与本地磁盘。模型、硬件和题集未选定，因此**实际 GPU 小时与费用未知**。用实测单题时间估算：GPU 小时 = 配置数×题数×重复次数×单题秒数/3600。例如 16 配置×200 题×1 次×假设 3 秒/题≈2.67 小时，只是预算示例，CPU 检索配置并不必然占用 GPU；三次重复约 8 小时，不能当作已测性能。首期只做部分配置则按实际配置数重算。

API 默认不使用，费用为 0；未来若需外部模型和数据传输，必须另获用户授权并先检查价格/数据权限。离线模型的权重磁盘取决于选型，本阶段不下载。审计派生库约数 GiB 以内，实际见文件；题集、证据日志另预留 1–5 GiB 的规划空间，序列文件保留原位置而不复制嵌入。

用户本阶段禁止启动实验和完整系统开发，因此不生成虚假的可运行训练/LLM 实验入口。当前真正可运行的只是审计命令：

```bash
python3 scripts/audit_data.py --out-dir reports/audit_recheck
python3 scripts/audit_followup.py --audit-dir reports/audit_recheck
python3 scripts/build_audit_reports.py --audit-dir reports/audit_recheck
```

后续实验 runner/config 必须在用户授权研究实现、选定模型和项目配置格式后编写。优先顺序：确认字典与真实问题→专家金标与只读查询→检索基线→固定证据的回答实验→再决定是否扩展表达/性状/多模态。
''')
    # 06/00：面向导师与下一阶段规划的自包含文档。
    keyfacts=f'''## 主要审计结论

1. 共 18 个 CSV，{byte_count/1024**2:.2f} MiB，{num(total)} 条记录；均完成全量解析、字段统计和前后 SHA-256 核对。没有修改或上传原始 CSV。
2. 主目录为 {num(entity_count)} 个实体：{num(standard)} 条标准 CSS 位点与 13 条非 CSS 命名实体。不能报告为 {num(entity_count)} 个茶树基因。
3. 序列覆盖全部 {num(standard)} 个标准 CSS 位点；功能注释覆盖 {num(field('gene_annotation','GeneID')['distinct_nonmissing'])} 个内部 ID，BLAST 覆盖 {num(field('annoblastpdetail','geneID')['distinct_nonmissing'])} 个，PFAM/IPR 覆盖 {num(field('annopfamdetail','geneID')['distinct_nonmissing'])} 个；引用覆盖和生物学语义确认分开。
4. 观测表 {num(by['gene_expro']['rows'])} 行，由标准 CSS 的 {num(standard)}×50 块和非 CSS 的 13×84 块组成。两个 sampleID 集合分别是 101..150 和 151..234，交集 0；val 的单位、样本含义与归一化未确认。
5. 10 张相关网络共有 {num(co_rows)} 条记录，每个 tag1 恰有 10 条边，不能当作完整对称相关矩阵或处理差异表达证据。
6. PFAM 表含 pfam {num(56865)} 条与 ipr {num(245660)} 条，存在多方法和少量业务重复；TF 分类包含 TF/TR 及 10 条 ARR-B/p15 特殊值；序列 Transcript 与 CDS 全部相同。
7. 转换表 50,525 行、10 个命名空间，SCZV2 后缀为 `.1` 到 `.6`，其中 732 行不是 `.1`。候选去后缀后与标准 CSS 集合吻合，且本批同 id 的主表基础编号全部一致、无基础编号错配；后缀不能一律补 `.1`，后缀/跨材料生物学定义待确认，反向存在多候选。
8. Gene 序列与 end−start+1 在 18,641 行长度不一致（更短）；Transcript=CDS 全部，AACount 与蛋白长存在系统差值。需要确认序列提取与坐标/UTR 规则，不能声称序列完整性已验证。

## 需要导师或数据提供者确认

1. 提供原始表 DDL/字段字典、导出日期/版本、来源 URL、许可和更新方式。Genome=3 对应什么物种、材料、assembly accession 和 annotation release？CSS 与 SCZV2 的关系及 `.1` 后缀是什么？
2. 50 个基因观测编号与 84 个非 CSS 观测编号分别是什么？请提供 sampleID→组织/处理/时点/生物重复/对照/研究批次/材料的元数据；val 分别使用什么单位、归一化和零值规则？两组编号是否有合法样本/材料映射？
3. GA、GC、EGC、C、EGCG、EC、GCG、ECG、Theanine、Caffeine、Polyphenol、ester_catechins、non_ester 的正式实体类别、含义与测量方法是什么？不能仅凭缩写扩写物质名。
4. 10 个网络代码各代表什么实验/样本？是否 top-10、按 r 还是 |r| 筛选？personR/personP 的精确定义、样本量、P 值校正、字面 0 与四舍五入规则？naci/shadding 是否原始命名？
5. BLAST s/m、mIdentity/sIdentity、pValue 的算法定义与库版本是什么？PFAM/IPR 的 mothod/eValue 解释、重复记录与 TF 的 ARR-B/p15 分类能否确认？GO/KO 是否有证据代码、原始方法与文献？
6. 是否希望优先服务“按 ID 汇总已有证据”还是某个明确的生物学问题？请提供 20–30 个真实科研问题、一名答案审核者、不可接受错误类型及下一阶段验收标准。

## 下一阶段优先级与验收成果

- P0：拿到版本/实体/sample 字典；明确 13 个非 CSS 实体和样本配对，形成经导师确认的数据语义清单。
- P1：在用户另行授权后，实现**版本化 CSS 基因证据档案与注释反查**：精确 ID、原字段、来源行、GO/KO、PFAM/IPR、BLAST、TF、序列与指定编号的 raw val；包含不可答和无结果处理。先 SQL/规则查询并建设 30–50 题本地金标。
- P2：比较全文/dense/混合检索与证据约束回答；收集独立专家测试题，测检索正确、版本错配、引用支持与答案覆盖。
- P3：样本元数据确认后再做组织/处理比较；基因—性状与多模态方向等待配对证据及实际新数据，不纳入当前可承诺功能。

本次仅完成数据审计、只读分析脚本、设计和实验计划。未开发 FastAPI/前端/RAG，未创建 PostgreSQL 表或迁移，未执行模型评测；没有可报告的模型准确率、论文创新结论或部署结果。
'''
    write('06_executive_summary.md','# 06 面向导师与项目负责人的总结\n\n'+intro+keyfacts+'''
## 建议的研究主线（待导师选择）

优先考虑 B/C 结合的“多源基因注释检索与证据忠实问答”，A 提供实体/版本保障。已有数据足够搭建受约束的证据查询与评估底座，但缺中文真实问题、专家金标、注释证据等级和文献。需要先把这些缺口变成可验收的数据与评测任务，再检索相关工作判断方法差异和创新空间。D 可作为元数据到位后的扩展，E 当前仅有有限化学成分命名线索，尚不具备样本配对或图像多模态基础。

## 报告与审计证据入口

- [01 数据文件/字段/质量](01_data_inventory.md)
- [02 ID 关系/重合/映射风险](02_data_relationships.md)
- [03 问答能力与限制](03_qa_capabilities.md)
- [04 PostgreSQL 设计文档](04_database_design.md)
- [05 科研方向与实验设计](05_research_opportunities.md)
- [00 供下一阶段 ChatGPT 规划的复核材料](00_review_for_chatgpt.md)
- [统计明细](audit_final/inventory.json)、[ID 重合矩阵](audit_final/id_overlap.csv)、[异常原记录索引](audit_final/anomaly_records.csv)、[补充统计](audit_final/followup.json)
''')
    keyfields=[
      ['chrom_tag.ID','内部目录键（TEXT）；包括 CSS 位点及 13 个非 CSS 实体','50,538 个唯一值；标准位点 ID=1..50525，其他 50552..50564'],
      ['chrom_tag.GeneName','50,525 个 CSS 编号 + 13 个非 CSS 名称','不是统一 gene symbol；需实体分类'],
      ['chrom_tag.Genome','值为 3 的来源代码','组装、物种、版本映射待确认'],
      ['chrom_tag.chrom/start/end/strand','位置、区间与链原始值','13 条非 CSS 为空；坐标制待确认'],
      ['chrom_tag.productName','已有原始蛋白/产物描述','5,008 个 NA + 13 个空串；非实验证明'],
      ['各表 geneID/GeneID/tag_id/chrom_tag_id','主目录内部键的候选引用字段','当前词法引用可覆盖；不统一为外部 GeneID'],
      ['gene_annotation.AnnoID','GO term 或 KO 编号','8,052 个不同编号；GO:.../K...'],
      ['gene_annotation.AnnoType','GO 三类与 Kegg Orthology','117,990 GO 记录 + 17,208 KO 记录'],
      ['gene_annotation.AnnoName','原始注释名称','短英文文本；没有实验文献/证据代码'],
      ['annoblastpdetail.accession/name','外部命中蛋白编号与描述','168,837 记录/37,448 内部 ID；不赋予本地 alias'],
      ['annoblastpdetail.mIdentity/sIdentity/pValue','比对数值原字段','Identity 在 0..1，分母和 pValue 类型待确认'],
      ['annopfamdetail.accession/iType','PFAM/IPR 原编号与类别','pfam 56,865；ipr 245,660'],
      ['annopfamdetail.mStart/mEnd/eValue/mothod','区间、分值与原始方法拼写','多方法；不能按 gene+accession 唯一'],
      ['gene_expro.sampleID','134 个编号的原始文本','标准 CSS 101..150；非 CSS 151..234，交集 0'],
      ['gene_expro.val','非负原始观测值','单位未确认；标准 CSS 与非 CSS 测量分层'],
      ['*_co.tag1/tag2','原有序基因对','每个 tag1 10 记录；不假设完整/对称'],
      ['*_co.personR/personP','相关形式数值/可能的检验量','方法/样本量/筛选规则待确认'],
      ['gene_id_convert.SCZV2 等 10 列','不同材料/版本原始标识符','SCZV2 后缀 .1..6（732 条非 .1），反向可多候选；禁止等价合并'],
      ['tfactor.tf_type/tf_name/tf_desc','原始类别、家族与层级描述','TF 2,345/TR 529/ARR-B 6/p15 4'],
      ['chrom_sequence.Protein/Transcript/CDS/Gene/Upstream/Downstream','长序列文本','50,525 行；Transcript=CDS 全部，非全染色体组装'],
    ]
    evidence_rows=[
      ['标准 CSS vs 非 CSS 实体','chrom_tag.GeneName/ID/chrom 与 gene_expro.geneID','50,525 / 13；13 个实体各 84 个原始值','inventory.checks.nonstandard_gene_names；followup.expression_entity_groups'],
      ['基因与非 CSS 样本不可直接配对','gene_expro.sampleID 与主表 ID','50 个 vs 84 个；交集 0','followup.expression_entity_groups.sample_id_overlap'],
      ['子表引用覆盖','geneID/GeneID/tag_id/chrom_tag_id vs chrom_tag.ID','具体交集/来源覆盖率见下表与完整 JSON','relationships.master_links'],
      ['ID 后缀候选映射','gene_id_convert.SCZV2 vs chrom_tag.GeneName','50,525 个去数字后缀候选基础 ID；无碰撞；后缀 .1..6','relationships.conversion_base、conversion_id_vs_master'],
      ['网络为 10 邻居形式导出','10 张 *_co.tag1/tag2','每个 tag1 恰 10；总 '+num(co_rows),'inventory.checks.<file>.outdegree_distribution'],
      ['PFAM 混合来源与重复','annopfamdetail.iType/mothod 与除 #/ID 的原行','pfam 56,865/ipr 245,660；3 组业务重复','inventory；followup.domain_duplicate_business_examples'],
      ['序列冗余与长度关系','chrom_sequence.Transcript/CDS/Protein + chrom_tag.AACount/start/end','Transcript=CDS 50,525 行；其他精确差值见 JSON','inventory.checks.sequence_crosschecks'],
    ]
    evidence_rows.append(['转换表后缀及基础编号一致性','gene_id_convert.id/SCZV2 vs chrom_tag.ID/GeneName','732 行非 .1；50,525 行基础编号一致，错配 0；后缀含义待确认','relationships.conversion_id_vs_master；followup.conversion_suffix_not_one_records'])
    evidence_rows.append(['完整跨表示形式比较','各引用字段经主目录投影 CSS；转换表候选去后缀','18 文件共 153 对；排除 13 个非 CSS 实体','followup.candidate_css_file_pairwise；candidate_css_file_overlap.csv'])
    core=['import_batch/source_file/source_record（溯源）','reference_context（物种/组装/注释版本，允许未决）','entity_record/entity_resolution（目录与词法/语义解析分层）','gene_locus（确认后的版本化标准位点）','gene_sequence_record/annotation_record/blast_hit/domain_hit/factor_assignment（原始证据）','observation_dataset/sample_record/raw_observation（基因与非 CSS 观测隔离、样本元数据可空）','network_dataset/network_edge（保留数据集与原有序边）','id_mapping_row/identifier_record/mapping_assertion（多候选映射断言）']
    write('00_review_for_chatgpt.md','# 00 供 ChatGPT 下一阶段架构规划的复核材料\n\n'+intro+'''
> 请以本报告和本地统计为事实输入。不要假定已有数据库/API/RAG 已实现，不把字段名当作生物学数据字典；任何新增实施应等待用户下一阶段明确授权。本阶段原始数据未上传；用户后续分享范围自行决定。

## 1. 全部数据文件简表

'''+summary_table+'\n## 2. 最关键的 20 个字段或字段组\n\n'+table(['文件.字段（同源字段合并展示）','实际表示','实测规模/限制'],keyfields)+'''
## 3. geneID 的真实形式与映射

主目录数字 `ID` 与其他表 geneID/GeneID/tag_id/chrom_tag_id 的**词法匹配**已核对；ID 读取和存储保持 TEXT。`CSS0000001` 是标准位点形式；转换表 `SCZV2=CSS0000001.1` 保留后缀。数据上 50,525 个候选基础 ID 与标准 CSS 集合吻合，`.1` 的转录本/版本含义与 Genome=3 尚未确认。不能把所有表的 ID 列互相连接，不能把其他物种命中描述的 GN 字段当作茶树 gene symbol。

转换表包含 SCZV2、Biyun、DASZ、HD、LJ43、SCZV1、TGY、YK10、COL、CCH；不是十个同一实体的无歧义别名。SCZV2 一行最多给每材料一个值，目标可被多行复用；其他材料间共同出现可多对多，不能传递推导正交或身份等价。公开 TPIA 材料物种命名可见 [官方共线性页](https://tpia.teaplants.cn/linear.html)，但本批精确版本、关系性质仍待数据提供者确认。

## 4. 规模与关联覆盖

'''+table(['来源','引用字段','唯一 ID','主目录匹配','来源覆盖','全部 50,538 目录覆盖','未匹配','例子'],linkrows)+'\n'+table(['转换命名空间','有值行','对 SCZV2 行覆盖','不同 ID','NA','最大复用次数'],convertrows)+'''
## 5. 可支持的主要问答

可支持后续开发：精确 CSS 基本信息/原始描述、GO/KO 注释及反查、BLASTP 已有命中、PFAM/IPR 原始区间与方法、TF/TR 家族、原始序列、指定 sampleID 的 raw val、指定网络代码下已有邻居、已有 ID 映射、多来源记录档案。

不能当前承诺：组织/处理语义比较、差异表达/显著上调下调、正交同源或基因功能实验证实、调控/因果、完整网络与通路、已配对的基因—代谢物关联、图像多模态。13 条非 CSS 名称及其 1,092 个值应保留，不误认为基因，也不误说完全没有化学成分线索。

精确 ID/数值/排序/过滤使用 SQL/规则；名称使用全文检索；中文语义检索和证据约束生成可后续比较。当前是短注释和结构化数据，不能把整个 CSV/长序列简单向量化后假定解决所有场景。

## 6. 推荐 PostgreSQL 核心结构

'''+''.join('- '+x+'。\n' for x in core)+'''

每条证据带 source_file/hash/import_batch/record_no/raw_fields。生物学 gene FK 在映射语义未确认时保持 NULL，候选词法引用单独保存。其他材料只有标识符而无主数据时，不创建虚构 gene_locus。13 个非 CSS 观测不能装入基因表达表。具体约束/索引和 ER 图见 [04](04_database_design.md)。本阶段未执行迁移。

## 7. 论文方向与主要问题

A：命名空间约束的实体链接，需真实歧义/别名语料；B：SQL/词法/语义混合检索，需中文问题和行级证据金标；C：来源标记、证据约束与拒答校准，需专家 claim-evidence 标注并注意注释来源依赖；D：联合查询可行，但表达语义研究需样本/单位元数据；E：有限化学命名实体存在，但缺配对和多模态资源。

优先讨论 B/C 主线与 A 支撑模块，比较公平基线、单变量消融、分组划分、盲测与置信区间。未做系统文献综述，**不将任何候选方向称为已证实创新**；没有模型效果或已运行实验。

'''+keyfacts+'''
## 8. 关键结论的证据索引

'''+table(['结论','来源文件/字段','统计依据','机器明细定位'],evidence_rows)+'''
## 9. 下一阶段实施前的验收输入

应先获得：确认的参考上下文与实体字典、样本说明表、20–30 个真实问题及答案审核者、首批 30–50 个查询金标、明确可答/不可答边界。获授权后优先实现可追溯的 CSS 证据档案与 GO/KO/domain 反查，再比较检索与回答策略。前端、向量模型和部署方案不应掩盖数据语义未确认问题。

## 10. 审计产物与复现

- [01 数据清单与全字段质量](01_data_inventory.md)
- [02 关联与 ID 重合](02_data_relationships.md)
- [03 问答能力](03_qa_capabilities.md)
- [04 数据库设计](04_database_design.md)
- [05 科研方向与实验计划](05_research_opportunities.md)
- [06 导师总结](06_executive_summary.md)
- [inventory.json](audit_final/inventory.json)、[relationships.json](audit_final/relationships.json)、[followup.json](audit_final/followup.json)、[id_overlap.csv](audit_final/id_overlap.csv)、[异常记录](audit_final/anomaly_records.csv)

审计脚本位于 `scripts/audit_data.py`、`scripts/audit_followup.py`，报告生成器为 `scripts/build_audit_reports.py`。只依赖 Python 标准库，Python 3.14.6 本地运行。复现需使用新 audit 输出目录，避免覆盖已有暂存库；raw 的全部文件指纹见 01。Markdown 与表格链接在本地检查；SQL、RAG、模型性能及生物学解释未验证。
''')
    print('\n'.join(str(root/name) for name in ['00_review_for_chatgpt.md','01_data_inventory.md','02_data_relationships.md','03_qa_capabilities.md','04_database_design.md','05_research_opportunities.md','06_executive_summary.md']))


if __name__=='__main__':main()
