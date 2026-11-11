"""可选的基准计时：只记录时长与次数，不记录 SQL 文本、参数或凭据。"""
from contextvars import ContextVar
from time import perf_counter

from sqlalchemy import event

samples = ContextVar('teageneqa_sql_timings', default=None)


def before_execute(conn,cursor,statement,parameters,context,executemany):
    context._teageneqa_started=perf_counter()


def after_execute(conn,cursor,statement,parameters,context,executemany):
    values=samples.get()
    if values is not None:values.append((perf_counter()-context._teageneqa_started)*1000)


def enable_timing(app,engine):
    if not event.contains(engine,'before_cursor_execute',before_execute):
        event.listen(engine,'before_cursor_execute',before_execute)
        event.listen(engine,'after_cursor_execute',after_execute)

    @app.middleware('http')
    async def timing(request,call_next):
        values=[];token=samples.set(values);started=perf_counter()
        try:
            response=await call_next(request)
            # db 是成功 cursor.execute 的累计耗时，包含数据库往返，排除取数/JSON 序列化/池等待。
            response.headers['Server-Timing']=f'db;dur={sum(values):.3f}, app;dur={(perf_counter()-started)*1000:.3f}'
            response.headers['X-DB-Query-Count']=str(len(values))
            return response
        finally:samples.reset(token)
