#!/usr/bin/env python3
"""只读扫描原始 CSV，生成可追溯的审计统计；不连接网络、不改动原始文件。"""

from __future__ import annotations

import argparse
import codecs
import csv
import hashlib
import json
import math
import re
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

csv.field_size_limit(sys.maxsize)
MISSING = {"", "na", "n/a", "nan", "null", "none", "\\n"}
INTEGER = re.compile(r"^[+-]?\d+$")
NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
SEQUENCES = {"Protein", "Transcript", "Gene", "Upstream", "Downstream", "CDS"}


def dump(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def missing(value):
    return value.strip().lower() in MISSING


def brief(value, limit=100):
    return value if len(value) <= limit else value[:limit] + f"…[长度={len(value)}]"


def inspect_bytes(path):
    start = path.stat()
    digest = hashlib.sha256()
    prefix = b""
    ascii_only = True
    valid_utf8 = True
    decoder = codecs.getincrementaldecoder("utf-8-sig")("strict")
    linefeeds = 0
    with path.open("rb") as handle:
        while block := handle.read(4 * 1024 * 1024):
            digest.update(block)
            linefeeds += block.count(b"\n")
            ascii_only = ascii_only and block.isascii()
            if not prefix:
                prefix = block[:131072]
            if valid_utf8:
                try:
                    decoder.decode(block)
                except UnicodeDecodeError:
                    valid_utf8 = False
        if valid_utf8:
            try:
                decoder.decode(b"", final=True)
            except UnicodeDecodeError:
                valid_utf8 = False
    if valid_utf8:
        encoding = "utf-8-sig" if prefix.startswith(codecs.BOM_UTF8) else "utf-8"
    else:
        encoding = None
        for candidate in ("utf-16", "gb18030", "big5"):
            try:
                with path.open(encoding=candidate) as handle:
                    while handle.read(1024 * 1024):
                        pass
                encoding = candidate
                break
            except (UnicodeError, LookupError):
                continue
        if encoding is None:
            raise ValueError(f"无法无损识别编码：{path}")
    sample = prefix.decode(encoding, errors="ignore")
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        delimiter = dialect.delimiter
        delimiter_method = "csv.Sniffer + 全文件列宽验证"
    except csv.Error:
        header = sample.splitlines()[0]
        delimiter = max(",\t;|", key=lambda sep: len(next(csv.reader([header], delimiter=sep))))
        delimiter_method = "表头候选分隔符比较 + 全文件列宽验证"
    return {"path": str(path), "bytes": start.st_size, "mtime_ns": start.st_mtime_ns,
            "sha256": digest.hexdigest(), "encoding": encoding,
            "encoding_note": "全文件 ASCII；兼容 UTF-8，无法仅凭字节区分其他 ASCII 兼容编码" if ascii_only else "全文件严格解码通过；不代表导出软件声明的编码",
            "ascii_only": ascii_only, "bom": prefix.startswith(codecs.BOM_UTF8),
            "delimiter": delimiter, "delimiter_method": delimiter_method,
            "physical_linefeeds": linefeeds}


class Profile:
    def __init__(self, name):
        self.name = name
        self.count = 0
        self.missing = Counter()
        self.types = Counter()
        self.examples = []
        self.sci = self.negative = self.zero = self.nonfinite = self.underflow = 0
        self.spaces = 0
        self.ambiguous_tokens = Counter()
        self.min_num = self.max_num = None
        self.total = 0.0
        self.max_len = 0
        self.lengths = Counter()
        self.invalid_alphabet = 0
        self.invalid_examples = []
        self.characters = Counter()
        self.mod3 = Counter()
        self.internal_stop = self.terminal_stop = 0

    def add(self, value):
        self.count += 1
        self.max_len = max(self.max_len, len(value))
        self.spaces += value != value.strip()
        if value.strip() in {"-", "."}:
            self.ambiguous_tokens[value] += 1
        if missing(value):
            self.missing[value] += 1
            return None
        if len(self.examples) < 3 and brief(value) not in self.examples:
            self.examples.append(brief(value))
        if self.name in SEQUENCES:
            self.types["sequence_text"] += 1
            self.lengths[len(value)] += 1
            self.characters.update(value)
            alphabet = "ACDEFGHIKLMNPQRSTVWYBXZJUO*" if self.name == "Protein" else "ACGTRYSWKMBDHVN"
            bad = set(value.upper()) - set(alphabet)
            if bad:
                self.invalid_alphabet += 1
                if len(self.invalid_examples) < 3:
                    self.invalid_examples.append({"characters": sorted(bad), "value": brief(value)})
            if self.name == "CDS":
                self.mod3[len(value) % 3] += 1
            if self.name == "Protein":
                self.internal_stop += "*" in value[:-1]
                self.terminal_stop += value.endswith("*")
            return hashlib.sha256(value.encode()).hexdigest()
        cleaned = value.strip()
        if NUMBER.fullmatch(cleaned):
            self.types["integer_text" if INTEGER.fullmatch(cleaned) else "decimal_text"] += 1
            self.sci += "e" in cleaned.lower()
            number = Decimal(cleaned)
            self.min_num = number if self.min_num is None else min(self.min_num, number)
            self.max_num = number if self.max_num is None else max(self.max_num, number)
            self.negative += number < 0
            self.zero += number == 0
            floating = float(number)
            if math.isfinite(floating):
                self.total += floating
                self.underflow += floating == 0 and number != 0
            else:
                self.nonfinite += 1
        elif cleaned.lower() in {"inf", "+inf", "-inf", "infinity", "+infinity", "-infinity"}:
            self.types["nonfinite_text"] += 1
            self.nonfinite += 1
        else:
            self.types["text"] += 1
        return value

    def result(self):
        nonmissing = self.count - sum(self.missing.values())
        out = {"field": self.name, "count": self.count, "nonmissing": nonmissing,
               "missing": sum(self.missing.values()),
               "missing_ratio": sum(self.missing.values()) / self.count if self.count else 0,
               "missing_tokens": dict(self.missing), "types": dict(self.types), "examples": self.examples,
               "scientific_notation": self.sci, "negative": self.negative, "zero": self.zero,
               "nonfinite": self.nonfinite, "float_underflow": self.underflow,
               "surrounding_whitespace": self.spaces, "max_length": self.max_len,
               "ambiguous_tokens_not_counted_missing": dict(self.ambiguous_tokens),
               "numeric_min": str(self.min_num) if self.min_num is not None else None,
               "numeric_max": str(self.max_num) if self.max_num is not None else None}
        if self.name in SEQUENCES:
            total = sum(self.lengths.values())
            def quantile(q):
                target = max(1, math.ceil(total * q))
                cumul = 0
                for length, count in sorted(self.lengths.items()):
                    cumul += count
                    if cumul >= target:
                        return length
            out["sequence"] = {"min_length": min(self.lengths) if total else None,
                               "median_length": quantile(.5) if total else None,
                               "p95_length": quantile(.95) if total else None,
                               "max_length": max(self.lengths) if total else None,
                               "mean_length": sum(k*v for k,v in self.lengths.items()) / total if total else None,
                               "total_characters": sum(k*v for k,v in self.lengths.items()),
                               "characters": dict(self.characters), "invalid_alphabet_rows": self.invalid_alphabet,
                               "invalid_examples": self.invalid_examples, "cds_length_mod3": dict(self.mod3),
                               "internal_stop_rows": self.internal_stop, "terminal_stop_rows": self.terminal_stop}
        return out


def row_hash(values):
    # 用长度定界，避免分隔符本身出现在字段中导致歧义。
    digest = hashlib.sha256()
    for value in values:
        payload = value.encode("utf-8")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def grouped_stats(conn, table, columns, examples=True):
    names = ",".join(columns)
    where = " AND ".join(f"{c} IS NOT NULL" for c in columns)
    query = f"SELECT {names},COUNT(*) AS n FROM {table} WHERE {where} GROUP BY {names}"
    n_groups = n_duplicate = excess = max_mult = 0
    duplicate_examples = []
    for row in conn.execute(query):
        n = row[-1]
        n_groups += 1
        max_mult = max(max_mult, n)
        if n > 1:
            n_duplicate += 1
            excess += n - 1
            if examples and len(duplicate_examples) < 5:
                duplicate_examples.append({"values": list(row[:-1]), "count": n})
    return {"distinct_nonmissing": n_groups, "duplicate_groups": n_duplicate,
            "duplicate_excess": excess, "max_multiplicity": max_mult,
            "duplicate_examples": duplicate_examples}


def cols(headers, fields):
    return ["c" + str(headers.index(f)) for f in fields]


def query_scalar(conn, query, args=()):
    return conn.execute(query, args).fetchone()[0]


def audit_file(conn, path, table, chunk_rows):
    meta = inspect_bytes(path)
    with path.open(encoding=meta["encoding"], newline="") as handle:
        reader = csv.reader(handle, delimiter=meta["delimiter"], strict=True)
        header = next(reader)
        if len(set(header)) != len(header):
            raise ValueError(f"重复字段名：{path}")
        profiles = [Profile(name) for name in header]
        column_names = [f"c{i}" for i in range(len(header))]
        conn.execute(f'CREATE TABLE {table} (_audit_row INTEGER PRIMARY KEY,{",".join(c+" TEXT" for c in column_names)},_full_hash TEXT,_content_hash TEXT,_business_hash TEXT)')
        is_sequence = "Protein" in header
        if is_sequence:
            conn.execute('CREATE TABLE sequence_lengths (audit_row INTEGER PRIMARY KEY,chrom_tag_id TEXT,protein_length INTEGER,transcript_length INTEGER,gene_length INTEGER,cds_length INTEGER)')
        insert = f'INSERT INTO {table} VALUES ({",".join("?" for _ in range(len(header)+4))})'
        payload = []
        sequence_payload = []
        malformed = []
        total_rows = hash_matches = id_matches = 0
        original_id = next((header.index(x) for x in ("ID", "id") if x in header), None)
        business = [i for i,name in enumerate(header) if name not in {"#", "ID", "id"}]
        for row_number, row in enumerate(reader, 1):
            total_rows += 1
            if len(row) != len(header):
                malformed.append({"record": row_number, "physical_end_line": reader.line_num, "width": len(row)})
                continue
            transformed = [profile.add(value) for profile, value in zip(profiles, row)]
            if is_sequence:
                sequence_payload.append([row_number, row[header.index('chrom_tag_id')]] + [len(row[header.index(name)]) for name in ['Protein','Transcript','Gene','CDS']])
            hash_matches += header[0] == "#" and row[0] == str(row_number)
            id_matches += original_id is not None and header[0] == "#" and row[original_id] == row[0]
            payload.append([row_number] + transformed + [row_hash(row), row_hash(row[1:] if header[0] == "#" else row), row_hash([row[i] for i in business])])
            if len(payload) >= chunk_rows:
                conn.executemany(insert, payload)
                if sequence_payload:
                    conn.executemany('INSERT INTO sequence_lengths VALUES (?,?,?,?,?,?)',sequence_payload)
                    sequence_payload.clear()
                conn.commit()
                payload.clear()
        if payload:
            conn.executemany(insert, payload)
            if sequence_payload:
                conn.executemany('INSERT INTO sequence_lengths VALUES (?,?,?,?,?,?)',sequence_payload)
            conn.commit()
    out = {**meta, "filename": path.name, "table": table, "headers": header,
           "rows": total_rows, "columns": len(header), "malformed": malformed,
           "hash_column_equals_record_number": hash_matches,
           "id_equals_hash_column": id_matches, "fields": []}
    for i, profile in enumerate(profiles):
        field = profile.result()
        field.update(grouped_stats(conn, table, [f"c{i}"]))
        field["observed_unique_nonmissing"] = field["missing"] == 0 and field["distinct_nonmissing"] == total_rows and not malformed
        if profile.name not in SEQUENCES:
            field["top_values"] = [{"value": brief(v), "count": n} for v,n in conn.execute(f'SELECT c{i},COUNT(*) n FROM {table} WHERE c{i} IS NOT NULL GROUP BY c{i} ORDER BY n DESC,c{i} LIMIT 6')]
        out["fields"].append(field)
    out["duplicates"] = {name: grouped_stats(conn, table, [column], examples=False)
                         for name,column in (("full_row", "_full_hash"), ("excluding_hash_column", "_content_hash"), ("excluding_export_and_surrogate_id", "_business_hash"))}
    compound = []
    if "sampleID" in header:
        compound.append(["geneID", "sampleID"])
    if "AnnoID" in header:
        compound.append(["GeneID", "AnnoType", "AnnoID"])
    if "tag1" in header:
        compound.append(["tag1", "tag2"])
    if "tf_name" in header:
        compound.append(["tag_id", "tf_type", "tf_name"])
    if "accession" in header:
        compound.append(["geneID", "accession", "mStart", "mEnd"])
    out["compound_keys"] = [{"fields": fields, **grouped_stats(conn,table,cols(header,fields))} for fields in compound]
    end = path.stat()
    out["stat_unchanged_after_parse"] = end.st_size == meta["bytes"] and end.st_mtime_ns == meta["mtime_ns"]
    return out


def extras(conn, files):
    by_name = {f["filename"]: f for f in files}
    result = {}
    for f in files:
        t,h = f["table"],f["headers"]
        def col(name): return cols(h,[name])[0]
        checks = {}
        if "tag1" in h:
            a,b,r,p = (col(x) for x in ["tag1","tag2","personR","personP"])
            checks["self_edges"] = query_scalar(conn,f"SELECT COUNT(*) FROM {t} WHERE {a}={b}")
            checks["r_outside_minus1_plus1"] = query_scalar(conn,f"SELECT COUNT(*) FROM {t} WHERE CAST({r} AS REAL) NOT BETWEEN -1 AND 1")
            checks["p_outside_0_1"] = query_scalar(conn,f"SELECT COUNT(*) FROM {t} WHERE CAST({p} AS REAL) NOT BETWEEN 0 AND 1")
            checks["negative_edges"] = query_scalar(conn,f"SELECT COUNT(*) FROM {t} WHERE CAST({r} AS REAL)<0")
            checks["abs_r_equal_one"] = query_scalar(conn,f"SELECT COUNT(*) FROM {t} WHERE ABS(CAST({r} AS REAL))=1")
            checks["abs_r_min"] = query_scalar(conn,f"SELECT MIN(ABS(CAST({r} AS REAL))) FROM {t}")
            checks["p_zero"] = query_scalar(conn,f"SELECT COUNT(*) FROM {t} WHERE CAST({p} AS REAL)=0")
            conn.execute(f"CREATE INDEX {t}_pair ON {t}({a},{b})")
            checks["reverse_rows"] = query_scalar(conn,f"SELECT COUNT(*) FROM {t} x WHERE EXISTS (SELECT 1 FROM {t} y WHERE y.{a}=x.{b} AND y.{b}=x.{a})")
            checks["unordered_unique_pairs"] = query_scalar(conn,f"SELECT COUNT(*) FROM (SELECT MIN({a},{b}),MAX({a},{b}) FROM {t} GROUP BY MIN({a},{b}),MAX({a},{b}))")
            checks["outdegree_distribution"] = dict(conn.execute(f"SELECT n,COUNT(*) FROM (SELECT {a},COUNT(*) n FROM {t} GROUP BY {a}) GROUP BY n"))
        if "AnnoType" in h:
            checks["annotation_types"] = [dict(zip(["type","rows","genes","terms"],row)) for row in conn.execute(f"SELECT {col('AnnoType')},COUNT(*),COUNT(DISTINCT {col('GeneID')}),COUNT(DISTINCT {col('AnnoID')}) FROM {t} GROUP BY {col('AnnoType')}")]
            checks["type_id_prefix_mismatches"] = query_scalar(conn,f"SELECT COUNT(*) FROM {t} WHERE ({col('AnnoType')}='Kegg Orthology' AND {col('AnnoID')} NOT GLOB 'K[0-9][0-9][0-9][0-9][0-9]') OR ({col('AnnoType')}!='Kegg Orthology' AND {col('AnnoID')} NOT GLOB 'GO:[0-9][0-9][0-9][0-9][0-9][0-9][0-9]')")
        if "chrom" in h:
            checks["gene_name_equals_css_padded_id"] = query_scalar(conn,f"SELECT COUNT(*) FROM {t} WHERE {col('GeneName')}='CSS'||printf('%07d',CAST({col('ID')} AS INTEGER))")
            checks["start_after_end"] = query_scalar(conn,f"SELECT COUNT(*) FROM {t} WHERE CAST({col('start')} AS INTEGER)>CAST({col('end')} AS INTEGER)")
            checks["nonpositive_coordinates"] = query_scalar(conn,f"SELECT COUNT(*) FROM {t} WHERE CAST({col('start')} AS INTEGER)<=0 OR CAST({col('end')} AS INTEGER)<=0")
            checks["exon_count_mismatches"] = 0
            checks["exon_outside_gene"] = 0
            for start,end,n,starts,ends in conn.execute(f"SELECT {','.join(col(x) for x in ['start','end','exonCount','exonStarts','exonEnds'])} FROM {t}"):
                if start is None or end is None or n is None:
                    continue
                ss = [int(v) for v in (starts or '').split(';') if v]
                ee = [int(v) for v in (ends or '').split(';') if v]
                checks["exon_count_mismatches"] += len(ss)!=int(n) or len(ee)!=int(n)
                checks["exon_outside_gene"] += any(x<int(start) or x>int(end) for x in ss+ee)
            checks["nonstandard_gene_names"] = [dict(zip(['record','ID','GeneName','chrom','start','end'],row)) for row in conn.execute(f"SELECT _audit_row,{','.join(col(x) for x in ['ID','GeneName','chrom','start','end'])} FROM {t} WHERE {col('GeneName')} NOT GLOB 'CSS[0-9][0-9][0-9][0-9][0-9][0-9][0-9]'")]
        if "pValue" in h:
            checks["blast_pvalue_over_one"] = query_scalar(conn,f"SELECT COUNT(*) FROM {t} WHERE CAST({col('pValue')} AS REAL)>1")
            checks["blast_identity_outside_0_1"] = {name:query_scalar(conn,f"SELECT COUNT(*) FROM {t} WHERE CAST({col(name)} AS REAL) NOT BETWEEN 0 AND 1") for name in ['mIdentity','sIdentity']}
            checks["blast_coordinate_checks"] = {"sStart_after_sEnd": query_scalar(conn,f"SELECT COUNT(*) FROM {t} WHERE CAST({col('sStart')} AS INTEGER)>CAST({col('sEnd')} AS INTEGER)"), "mStart_after_mEnd":query_scalar(conn,f"SELECT COUNT(*) FROM {t} WHERE CAST({col('mStart')} AS INTEGER)>CAST({col('mEnd')} AS INTEGER)")}
        if "eValue" in h:
            checks["pfam_evalue_over_one"] = query_scalar(conn,f"SELECT COUNT(*) FROM {t} WHERE CAST({col('eValue')} AS REAL)>1")
            checks["domain_start_after_end"] = query_scalar(conn,f"SELECT COUNT(*) FROM {t} WHERE CAST({col('mStart')} AS INTEGER)>CAST({col('mEnd')} AS INTEGER)")
        result[f['filename']] = checks
    seq = by_name['tpia_chrom_sequence_css.csv']; gene = by_name['tpia_chrom_tag_css.csv']
    st,gt=seq['table'],gene['table']; sh,gh=seq['headers'],gene['headers']
    s=lambda n:'s.'+cols(sh,[n])[0]
    g=lambda n:'g.'+cols(gh,[n])[0]
    result['sequence_crosschecks']={
        'transcript_equals_cds_rows':query_scalar(conn,f"SELECT COUNT(*) FROM {st} WHERE {cols(sh,['Transcript'])[0]}={cols(sh,['CDS'])[0]}"),
        'id_equals_chrom_tag_id_rows':query_scalar(conn,f"SELECT COUNT(*) FROM {st} WHERE {cols(sh,['id'])[0]}={cols(sh,['chrom_tag_id'])[0]}")}
    result['sequence_crosschecks']['aa_count_minus_protein_length'] = dict(conn.execute(f"SELECT CAST({g('AACount')} AS INTEGER)-s.protein_length,COUNT(*) FROM sequence_lengths s JOIN {gt} g ON s.chrom_tag_id={g('ID')} GROUP BY 1"))
    result['sequence_crosschecks']['gene_length_minus_inclusive_coordinate_span'] = dict(conn.execute(f"SELECT s.gene_length-(CAST({g('end')} AS INTEGER)-CAST({g('start')} AS INTEGER)+1),COUNT(*) FROM sequence_lengths s JOIN {gt} g ON s.chrom_tag_id={g('ID')} GROUP BY 1"))
    result['sequence_crosschecks']['cds_length_minus_3protein'] = dict(conn.execute('SELECT cds_length-3*protein_length,COUNT(*) FROM sequence_lengths GROUP BY 1'))
    return result


def relationships(conn, files):
    conn.execute('CREATE TABLE idsets (dataset TEXT,field TEXT,value TEXT,PRIMARY KEY(dataset,field,value)) WITHOUT ROWID')
    targets=[]
    for f in files:
        h,t=f['headers'],f['table']
        fields=[v for v in ['geneID','GeneID','tag_id','chrom_tag_id','tag1','tag2','GeneName'] if v in h]
        if f['filename']=='tpia_chrom_tag_css.csv': fields.append('ID')
        if 'SCZV2' in h: fields.extend(h[2:])
        for field in fields:
            c=cols(h,[field])[0]
            conn.execute(f'INSERT INTO idsets SELECT ?,?,{c} FROM {t} WHERE {c} IS NOT NULL GROUP BY {c}',(f['filename'],field))
            targets.append((f['filename'],field))
        if 'tag1' in h:
            conn.execute('INSERT OR IGNORE INTO idsets SELECT dataset,?,value FROM idsets WHERE dataset=? AND field IN (?,?)',('nodes',f['filename'],'tag1','tag2'))
            targets.append((f['filename'],'nodes'))
    conversion=next(f for f in files if 'SCZV2' in f['headers'])
    # 只登记可逆的字符串候选变换；是否表示同一实体单独标为待确认。
    base_collisions=Counter()
    for value, in conn.execute('SELECT value FROM idsets WHERE dataset=? AND field=?',(conversion['filename'],'SCZV2')):
        match=re.fullmatch(r'(CSS\d+)\.(\d+)',value)
        if match: base_collisions[match.group(1)]+=1
    conn.executemany('INSERT INTO idsets VALUES (?,?,?)',[(conversion['filename'],'SCZV2_candidate_base',value) for value in base_collisions])
    targets.append((conversion['filename'],'SCZV2_candidate_base'))
    conn.commit()
    result={'idsets':[],'pairwise':[],'master_links':[], 'conversion_base':{'distinct':len(base_collisions),'colliding_bases':sum(v>1 for v in base_collisions.values()),'max_suffix_variants':max(base_collisions.values(),default=0)}}
    values={}
    for dataset,field in targets:
        # 每次仅保留 ID 集合，不保留全行和序列，规模由 distinct ID 决定。
        s={r[0] for r in conn.execute('SELECT value FROM idsets WHERE dataset=? AND field=?',(dataset,field))}
        values[(dataset,field)]=s
        result['idsets'].append({'file':dataset,'field':field,'count':len(s),'examples':sorted(s)[:5]})
    master_name='tpia_chrom_tag_css.csv'
    masters={'internal':values[(master_name,'ID')],'css':values[(master_name,'GeneName')]}
    for (dataset,field),s in values.items():
        if dataset == conversion['filename'] and field != 'SCZV2_candidate_base':
            # 其他材料不属于本地主目录命名空间；不能把它们误报为外键孤儿。
            continue
        namespace='internal' if field in {'geneID','GeneID','tag_id','chrom_tag_id','ID'} else 'css'
        master=masters[namespace]
        matched=s & master
        result['master_links'].append({'file':dataset,'field':field,'namespace':namespace,'distinct':len(s),'matched':len(matched),'coverage_of_source':len(matched)/len(s) if s else None,'coverage_of_master':len(matched)/len(master),'unmatched_count':len(s-master),'unmatched_examples':sorted(s-master)[:10]})
    # 完整两两重合矩阵仅比较相同词法命名空间；转换表其他材料另行评估。
    comparable=[k for k in values if k[1] in {'geneID','GeneID','tag_id','chrom_tag_id','ID','GeneName','nodes','SCZV2_candidate_base'}]
    for i,a in enumerate(comparable):
        for b in comparable[i+1:]:
            an='internal' if a[1] in {'geneID','GeneID','tag_id','chrom_tag_id','ID'} else 'css'
            bn='internal' if b[1] in {'geneID','GeneID','tag_id','chrom_tag_id','ID'} else 'css'
            if an!=bn:continue
            sa,sb=values[a],values[b];n=len(sa&sb)
            result['pairwise'].append({'a_file':a[0],'a_field':a[1],'b_file':b[0],'b_field':b[1],'a_distinct':len(sa),'b_distinct':len(sb),'intersection':n,'coverage_a':n/len(sa) if sa else None,'coverage_b':n/len(sb) if sb else None,'a_only':len(sa-sb),'b_only':len(sb-sa)})
    h,t=conversion['headers'],conversion['table']
    result['conversion_pairs']=[]
    for i,a in enumerate(h[2:]):
        for b in h[2+i+1:]:
            ca,cb=cols(h,[a,b]);pairs=list(conn.execute(f'SELECT {ca},{cb},COUNT(*) FROM {t} WHERE {ca} IS NOT NULL AND {cb} IS NOT NULL GROUP BY {ca},{cb}'))
            da,db=Counter(),Counter()
            for va,vb,n in pairs: da[va]+=1;db[vb]+=1
            result['conversion_pairs'].append({'a':a,'b':b,'nonmissing_rows':sum(x[2] for x in pairs),'distinct_pairs':len(pairs),'a_ids':len(da),'b_ids':len(db),'a_multiple_targets':sum(v>1 for v in da.values()),'b_multiple_sources':sum(v>1 for v in db.values()),'max_targets_per_a':max(da.values(),default=0),'max_sources_per_b':max(db.values(),default=0)})
    result['conversion_id_vs_master']={}
    ca,ci=cols(h,['SCZV2','id'])
    gfile=next(f for f in files if f['filename']==master_name);gt=gfile['table'];gid,gname=cols(gfile['headers'],['ID','GeneName'])
    result['conversion_id_vs_master']['id_join_rows']=query_scalar(conn,f'SELECT COUNT(*) FROM {t} x JOIN {gt} g ON x.{ci}=g.{gid}')
    result['conversion_id_vs_master']['same_id_implies_css_dot1_rows']=query_scalar(conn,f"SELECT COUNT(*) FROM {t} x JOIN {gt} g ON x.{ci}=g.{gid} WHERE x.{ca}=g.{gname}||'.1'")
    result['conversion_id_vs_master']['not_literal_dot1_rows'] = result['conversion_id_vs_master']['id_join_rows'] - result['conversion_id_vs_master']['same_id_implies_css_dot1_rows']
    result['conversion_id_vs_master']['id_matches_candidate_base_rows'] = query_scalar(conn,f"SELECT COUNT(*) FROM {t} x JOIN {gt} g ON x.{ci}=g.{gid} WHERE SUBSTR(x.{ca},1,INSTR(x.{ca},'.')-1)=g.{gname}")
    result['conversion_id_vs_master']['id_base_mismatches'] = result['conversion_id_vs_master']['id_join_rows'] - result['conversion_id_vs_master']['id_matches_candidate_base_rows']
    result['conversion_id_vs_master']['suffix_distribution'] = dict(conn.execute(f"SELECT SUBSTR({ca},INSTR({ca},'.')+1),COUNT(*) FROM {t} GROUP BY 1"))
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw-dir',type=Path,default=Path('data/raw'))
    parser.add_argument('--out-dir',type=Path,default=Path('reports/audit'))
    parser.add_argument('--chunk-rows',type=int,default=5000)
    args=parser.parse_args()
    raw=args.raw_dir.resolve();out=args.out_dir.resolve()
    if out==raw or raw in out.parents:raise ValueError('输出目录不得位于原始数据目录内')
    files=sorted(p for p in raw.rglob('*') if p.is_file() and p.suffix.lower()=='.csv')
    if not files:raise ValueError('没有找到 CSV')
    out.mkdir(parents=True,exist_ok=True)
    db=out/'audit_work.sqlite'
    if db.exists():raise FileExistsError('审计暂存库已存在；请使用新的 --out-dir，避免覆盖已有审计')
    conn=sqlite3.connect(db)
    conn.execute('PRAGMA journal_mode=OFF');conn.execute('PRAGMA synchronous=OFF')
    conn.execute('PRAGMA cache_size=-32768');conn.execute('PRAGMA temp_store=FILE')
    start=time.monotonic()
    audit={'created_at':datetime.now().astimezone().isoformat(),'python':sys.version,'sqlite':sqlite3.sqlite_version,'raw_dir':str(raw),'chunk_rows':args.chunk_rows,'missing_policy':sorted(MISSING),'files':[],'excluded_non_csv':[str(p.relative_to(raw)) for p in sorted(raw.rglob('*')) if p.is_file() and p.suffix.lower()!='.csv']}
    for i,path in enumerate(files):
        print(f'[{i+1}/{len(files)}] {path.name}',flush=True)
        audit['files'].append(audit_file(conn,path,f't{i:02d}',args.chunk_rows))
        dump(out/'inventory.json',audit)
        print(f'  rows={audit["files"][-1]["rows"]:,}; elapsed={time.monotonic()-start:.1f}s',flush=True)
    print('Cross-table and quality checks',flush=True)
    audit['checks']=extras(conn,audit['files'])
    dump(out/'inventory.json',audit)
    rel=relationships(conn,audit['files']);dump(out/'relationships.json',rel)
    with (out/'id_overlap.csv').open('w',encoding='utf-8-sig',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=list(rel['pairwise'][0]));writer.writeheader();writer.writerows(rel['pairwise'])
    print('Rehashing raw inputs',flush=True)
    for f in audit['files']:
        current=inspect_bytes(Path(f['path']))
        f['sha256_after']=current['sha256'];f['raw_unchanged']=f['sha256']==current['sha256'] and f['bytes']==current['bytes'] and f['mtime_ns']==current['mtime_ns']
    audit['duration_seconds']=time.monotonic()-start
    audit['script_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    dump(out/'inventory.json',audit)
    conn.close()
    print(f'Completed: {out}; elapsed={audit["duration_seconds"]:.1f}s',flush=True)


if __name__=='__main__':
    main()
