"""中文科研查询页：所有事实来自本地受控 API。"""
import math
import os

import requests
import streamlit as st

API_URL=os.getenv('TEAGENEQA_API_URL','http://127.0.0.1:18000').rstrip('/')
st.set_page_config(page_title='TeaGeneQA',layout='wide')
st.title('TeaGeneQA · 茶树基因证据查询')
st.caption('查询数据库已有记录。参考版本、样本含义与单位的未决项会随结果展示。')


def call_api(path,params=None,body=None):
    try:
        if body is None:response=requests.get(API_URL+'/api/v1'+path,params=params,timeout=15)
        else:response=requests.post(API_URL+'/api/v1'+path,json=body,timeout=15)
        data=response.json()
        if not response.ok:
            error=data.get('error',{})
            st.error(error.get('message',f'接口返回 HTTP {response.status_code}'))
            if error.get('details'):st.json(error['details'])
            return None
        return data
    except (requests.RequestException,ValueError):
        st.error('无法连接查询服务，请检查后端运行状态和 TEAGENEQA_API_URL。')
        return None


def table_rows(page):
    return [{**item['raw_fields'],'来源文件':item['provenance']['filename'],
             '原始记录号':item['provenance']['record_no'],'来源文件ID':item['provenance']['source_file_id'],
             '文件SHA256':item['provenance']['file_sha256'],'证据类别':item['evidence_class'],
             '语义状态':item['semantic_status']} for item in page['items']]


contexts=call_api('/contexts',{'limit':200})
if contexts is None:st.stop()
context_items=list(contexts['items'])
while len(context_items)<contexts['total']:
    more=call_api('/contexts',{'limit':200,'offset':len(context_items)})
    if not more or not more['items']:break
    context_items.extend(more['items'])
labels={c['key']:c['label'] for c in context_items}
options=[None]+list(labels)
with st.form('gene_search'):
    query_id=st.text_input('标准 CSS 基因 ID',value='CSS0000001',help='使用完整位点编号；原始数字后缀请使用映射接口核查。')
    selected_context=st.selectbox('数据上下文',options,index=1 if context_items else 0,
        format_func=lambda key:'自动选择（仅适用于无歧义 ID）' if key is None else f'{labels[key]} · {key}')
    submitted=st.form_submit_button('查询基因')
if submitted:
    params={'limit':20}
    if selected_context:params['context_key']=selected_context
    result=call_api(f'/genes/{query_id.strip()}/profile',params)
    if result is not None:st.session_state['profile']=result
    else:st.session_state.pop('profile',None)

if 'profile' in st.session_state:
    result=st.session_state['profile'];gene=result['gene'];gene_id=gene['gene_id'];key=gene['context_key']
    st.subheader(gene_id+' · 基本信息')
    st.json({'context_key':key,'参考元数据':gene['reference_metadata'],'语义状态':gene['semantic_status']})
    st.dataframe([gene['raw_fields']],hide_index=True,width='stretch')
    st.warning('当前未提供实验验证功能证据；同源比对、结构域和功能注释只按来源记录展示。')
    with st.expander('基本信息的原始来源'):
        st.json(gene['provenance'])
    sections=[('annotation','GO / KO 注释','annotations'),('domain','PFAM / IPR','domains'),
              ('blast','BLASTP','blast'),('factor','TF / TR','factors'),
              ('expression','原始观测值','expression'),('mapping','ID 映射','mappings'),('network','相关网络','networks')]
    tabs=st.tabs([label for _,label,_ in sections]+['序列（按需）','记录追溯'])
    for tab,(kind,label,route) in zip(tabs,sections):
        with tab:
            original=result['evidence'][kind];size=20;params={'context_key':key,'limit':size}
            prefix=f'{key}:{gene_id}:{kind}'
            if kind=='expression':
                st.info('这里只展示 sampleID 和原始 val；单位、组织、处理及归一化待确认。')
                sample=st.text_input('sampleID（留空查看全部）',key=prefix+':sample')
                if sample:params['sampleID']=sample.strip()
            if kind=='network':st.caption('按 tag1 出边查询；不同数据集保留原代码，不解释为完整网络或调控关系。')
            # 输入样本编号后先获取精确总量，再计算分页范围。
            first=call_api(f'/genes/{gene_id}/{route}',params) if kind=='expression' and params.get('sampleID') else original
            if first is None:continue
            total=first['total'];pages=max(1,math.ceil(total/size))
            page=st.number_input('页码',min_value=1,max_value=pages,value=1,step=1,key=prefix+':page:'+params.get('sampleID','all'))
            params['offset']=(page-1)*size
            data=call_api(f'/genes/{gene_id}/{route}',params) if page>1 else first
            if data is None:continue
            if data['source_file_count']==0:st.warning('当前上下文尚未导入此类来源文件；空结果不能解释为缺少相应功能。')
            st.caption(f"共 {total} 条；当前第 {page}/{pages} 页，每页最多 {size} 条。")
            if data['items']:st.dataframe(table_rows(data),hide_index=True,width='stretch')
            else:st.info('本快照未检索到相应记录，不能据此认定生物学上不存在。')
    with tabs[-2]:
        st.caption(f"已存序列记录 {result['sequence_records']} 条；普通档案不加载长序列。")
        sequence_type=st.selectbox('序列类型',['Protein','CDS','Transcript','Gene','Upstream','Downstream'])
        if st.button('读取原始序列'):
            data=call_api(f'/genes/{gene_id}/sequences/{sequence_type}',{'context_key':key,'limit':20})
            if data:
                if not data['items']:st.info('没有序列记录')
                for index,item in enumerate(data['items']):
                    value=item['raw_fields'][sequence_type]
                    st.text_area(f'{sequence_type} 原始序列（{len(value)} 字符）',value,height=160,key=f'sequence:{index}')
                    st.json(item['provenance'])
    with tabs[-1]:
        with st.form('trace_record'):
            source_id=st.number_input('来源文件 ID',min_value=1,value=gene['provenance']['source_file_id'],step=1)
            record_no=st.number_input('原始数据记录号',min_value=1,value=gene['provenance']['record_no'],step=1)
            trace=st.form_submit_button('查看原始记录')
        if trace:
            data=call_api(f'/sources/{source_id}/records/{record_no}')
            if data:st.json(data)

st.divider()
st.subheader('受控问题查询')
st.caption('无需配置 LLM API。支持一个 CSS ID 的基本信息、GO/KO、PFAM/IPR、BLASTP 和指定 sampleID 原值查询。')
with st.form('controlled_query'):
    question=st.text_input('问题',value='查询 CSS0000001 在 sampleID=101 的原始数值')
    ask=st.form_submit_button('提交问题')
if ask:
    answer=call_api('/query',body={'question':question,'context_key':selected_context})
    if answer:
        st.write(answer['answer'])
        if answer.get('result'):
            with st.expander('查询工具返回的证据与来源'):st.json(answer['result'])
        for note in answer.get('limitations',[]):st.caption(note)
