# TeaGeneQA V1 部署说明

## 运行配置

Python 3.11+，当前使用 `conda activate llm-study`。数据库必须为 PostgreSQL，迁移与 JSONB/COPY/约束不使用 SQLite 替代。服务不自动建表，先执行 Alembic。

| 环境变量 | 本机默认值/作用 |
| --- | --- |
| DATABASE_URL | 可选；显式连接串，优先于下列 Docker 字段；Compose 内使用 `db:5432` |
| TEAGENEQA_DATABASE_MODE | 默认 `docker`；显式设为 `native` 时使用保留的原生库 55432 |
| POSTGRES_PASSWORD | Docker 认证密码，从项目 `.env` 读取，不输出 |
| TEAGENEQA_DB_PORT | Docker 宿主映射端口，默认 `55433` |
| TEAGENEQA_API_URL | Streamlit 查询的 API，默认 `http://127.0.0.1:18000` |
| TEAGENEQA_TEST_ADMIN_URL | 可选的独立测试数据库管理连接 |
| TEAGENEQA_FULL_DATA | `1` 时开启真实开发库只读复核测试 |

Python 本机运行自动读取项目根目录 `.env`，不依赖当前终端目录。优先级为：进程 `DATABASE_URL` → `.env` 中显式 `DATABASE_URL` → Docker/原生模式与各字段（同名进程变量优先于文件）。默认模式是 Docker，缺少密码或连接失败均报错，不静默退回旧库。若终端以前设置过旧连接串，先 `unset DATABASE_URL` 再启动服务。

配置支持 `KEY=value`、空行、整行注释及成对单/双引号，不执行 shell、不展开 `${...}`、不解析行内注释。密码通过 SQLAlchemy URL 对象编码，保留特殊字符；文件权限应为 0600。不要把带密码连接串写进代码、报告或 shell 日志。`.env` 和 `.runtime` 均已忽略。

## 本机 PostgreSQL

本次已在 `.runtime/postgres` 安装隔离的 PostgreSQL 17，并在 `.runtime/pgdata` 初始化专属开发集群。新电脑可用以下命令准备同一目录；若已有 PostgreSQL，设置其开发库连接即可，不需要重复安装。

```bash
conda create --prefix "$PWD/.runtime/postgres" --override-channels --channel conda-forge postgresql=17 --no-default-packages -y
conda activate llm-study
python -m pip install -r requirements-dev.txt
python scripts/dev_postgres.py start
export TEAGENEQA_DATABASE_MODE=native
python -m alembic upgrade head
python -m teageneqa.importer --manifest config/import_manifest.json
```

这是可选恢复路径；当前常用数据库为 Docker。开发脚本固定监听 `127.0.0.1:55432`，使用仅本机开发的 trust 认证。它不能作为对外数据库配置。未替换其他项目数据库、未启用原生库开机自启。回到 Docker 时执行 `unset TEAGENEQA_DATABASE_MODE DATABASE_URL`，再重启 API。

```bash
python scripts/dev_postgres.py status
python scripts/dev_postgres.py stop
```

停止保留数据。不要删除 `.runtime/pgdata` 来解决迁移错误。升级前可使用本项目 `pg_dump` 生成可恢复副本；迁移降级明确禁止自动删除表。

## Docker Compose

需已安装 Docker Engine/Desktop 与 Compose。2026-11-10 已安装并启动 Docker Desktop 4.91.0，Docker Engine 29.8.0、Compose v5.5.1。日常开发采用 **Docker PostgreSQL 17.11 + 宿主机 Python API/Streamlit**，仅运行 `db` 容器。完整应用容器在下文的隔离环境中验证。

当前数据库容器为 `teageneqa-v1-db-1`，地址 `127.0.0.1:55433`，数据库与用户分别为 `teageneqa_dev` / `teageneqa`，密码保存在项目 `.env`。数据卷 `teageneqa-v1_pgdata` 持久化到容器 `/var/lib/postgresql/data`；`restart: unless-stopped` 用于 Docker 引擎启动后的容器恢复。原生开发库继续使用 55432，二者互相独立。安装与迁移验收记录保存在研究工作区的 `reports/`。

仅管理本次安装的数据库容器：

```bash
docker --context desktop-linux compose up -d --wait db
docker --context desktop-linux compose ps db
docker --context desktop-linux compose exec db psql -U teageneqa -d teageneqa_dev
docker --context desktop-linux compose stop db
```

以下是完整应用容器部署步骤，目前仍未执行；启动前先确认原生 API/Web 端口占用与数据迁移方案：

```bash
conda activate llm-study
python scripts/setup_compose_env.py
docker compose config --quiet
docker compose up --build -d
docker compose ps
docker compose logs importer
```

`setup_compose_env.py` 以排他创建方式写 `.env`，只生成随机本地密码，不打印密码；如果已有 `.env` 则拒绝覆盖。已有配置的用户可自己添加 `POSTGRES_PASSWORD`，建议使用 URL-safe 随机值。

启动顺序：数据库健康检查通过 → Alembic 成功 → CSV 导入成功 → API → Streamlit。原始目录仅通过 `:ro` 绑定给导入器，Docker 构建上下文排除整个 `data` 和 `reports`。PostgreSQL 使用 `pgdata` 命名卷；导入错误报告使用 `import_reports` 卷。文件级失败会阻止新的 API 容器启动，可查看导入日志，修正新清单后重跑。

默认端口：Web 18501、API 18000、PostgreSQL 55433，均绑定 127.0.0.1。原生数据库用 55432，与 Compose 数据库互相独立；API/Web 若已原生启动，应先在其终端 Ctrl+C，或通过 `TEAGENEQA_API_PORT` / `TEAGENEQA_WEB_PORT` 改用其他宿主端口。

```bash
docker compose run --rm importer
docker compose cp importer:/app/reports/v1_runtime ./reports/compose_runtime
docker compose stop
```

`stop` 保留命名卷。不要为重试导入执行 `down -v`。新增数据使用新上下文清单，不能修改旧数据库快照来掩盖冲突。

目前是本机科研开发系统。公开部署需要另行设计认证、TLS、只读应用角色、数据库备份策略与部署环境，不在本次已验证范围内。

## V1.1 隔离验证

V1.1 已在独立 Compose 项目中验证镜像构建、真实 CSV 全量导入、数据库/API/Web 健康检查和 PostgreSQL 容器重建后的数据保留。覆盖文件为 `compose.quality.yaml`，使用显式独立项目名、数据卷和 18110/18511/55434 端口。覆盖文件使用 `!override` 替换端口列表，需要 Docker Compose 2.24.4+；本机实际为 v5.5.1。[Compose 合并规则](https://docs.docker.com/reference/compose-file/merge/)

隔离环境完成验收后停止容器、保留卷；`python scripts/v11_deployment.py resume` 可恢复当前快照的验证环境，`stop` 只停止该项目。具体命令见[候选评测说明](../evaluations/v1_1/README.md)，实测结果保存在研究工作区的 `reports/08_v1_1_quality_review.md`。验收没有重启 18000/18501 的日常服务；它们在按 README 重启时加载 V1.1 代码。

Compose 配置依据官方 [启动依赖顺序](https://docs.docker.com/compose/how-tos/startup-order/) 与 [环境变量插值](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/)；静态 Schema 来源为 [compose-spec](https://github.com/compose-spec/compose-spec/blob/master/schema/compose-spec.json)。
