from streamlit.testing.v1 import AppTest

from conftest import ROOT


def test_streamlit_search_and_raw_expression(client,monkeypatch):
    # 页面 HTTP 调用接到真实 PostgreSQL 上的 TestClient，不硬编码服务答案。
    import requests
    def adapt(response):
        result=requests.Response();result.status_code=response.status_code
        result._content=response.content;result.encoding='utf-8'
        return result
    monkeypatch.setattr(requests,'get',lambda url,params=None,timeout=None:adapt(client.get('/api/v1'+url.split('/api/v1')[1],params=params)))
    monkeypatch.setattr(requests,'post',lambda url,json=None,timeout=None:adapt(client.post('/api/v1'+url.split('/api/v1')[1],json=json)))
    app=AppTest.from_file(str(ROOT/'ui/app.py'),default_timeout=30).run()
    assert not app.exception
    # 自动选择含多个上下文时，显式选择真实夹具，避免将单次缓存当作版本确认。
    app.selectbox[0].select('real_fixture')
    app.text_input[0].set_value('CSS0000001')
    app.button[0].click().run()
    assert not app.exception
    assert any('GeneName' in frame.value.columns and 'CSS0000001' in list(frame.value['GeneName']) for frame in app.dataframe), {'errors':[x.value for x in app.error],'dataframes':[list(x.value.columns) for x in app.dataframe],'state':str(app.session_state)}
    expression=next(widget for widget in app.text_input if widget.label.startswith('sampleID'))
    expression.set_value('101').run()
    assert not app.exception
    assert any('sampleID' in frame.value.columns and list(frame.value['sampleID'])==['101'] for frame in app.dataframe)
