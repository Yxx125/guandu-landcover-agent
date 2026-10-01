# 官渡区土地覆盖系统部署说明

复制 `.env.example` 为 `.env`，修改密码和模型配置后执行 `start.ps1`（Linux 执行 `start.sh`）。默认地址是 `http://127.0.0.1:8088`。

启动脚本会初始化 PostgreSQL、GeoServer、Neo4j、Chroma、FastAPI 和 Nginx。部署后执行 `webgis_system/scripts/10_acceptance.py` 与 `12_query_regression.py`，并在浏览器检查地图、图例、图表和问答联动。

## 模型提供者切换（PowerShell）

在 `F:\官渡区` 执行以下命令。先复制 `.env.example` 为 `.env`，设置数据库和 Neo4j 密码。`MODEL_PROVIDER` 仅允许 `none`、`ollama`、`cloud`，缺省是 `none`。模型切换后须重建后端容器环境；代码更新后增加 `--build`。切换不会删除数据库、栅格或 GeoServer 数据卷。

禁用模型（默认；统计、地图和可确定解析的问题仍可用，需模型规划的问题返回“当前未启用模型”）：

```powershell
$env:MODEL_PROVIDER='none'
docker compose up -d --force-recreate backend frontend
```

本机 Ollama（先在 Windows 启动 Ollama，并用 `ollama list` 确认模型已存在；无需再下载或启动 Docker 中的 Ollama）：

```powershell
$env:MODEL_PROVIDER='ollama'
$env:OLLAMA_BASE_URL='http://host.docker.internal:11434'
$env:OLLAMA_MODEL='qwen2.5:7b'
docker compose up -d --force-recreate backend frontend
```

云端 API（在本机运行环境中注入密钥，不写入 Dockerfile、镜像或版本库）：

```powershell
$env:MODEL_PROVIDER='cloud'
$env:MODEL_API_BASE_URL='https://你的服务商地址/v1'
$env:MODEL_NAME='服务商实际模型名'
$env:MODEL_API_KEY=Read-Host '输入 API Key'
docker compose up -d --force-recreate backend frontend
```

`MODEL_API_BASE_URL` 填 OpenAI 兼容 API 根路径，程序追加 `/chat/completions`。这些 PowerShell 变量只作用于当前终端；若 `.env` 中也有同名字段，当前终端变量优先。云端密钥通过容器运行时环境传入，具有 Docker 管理权限的人仍可查看容器环境，须妥善控制主机权限。切回禁用模式时清除当前会话中的云端变量：`Remove-Item Env:MODEL_API_KEY,Env:MODEL_API_BASE_URL,Env:MODEL_NAME -ErrorAction SilentlyContinue`。

网页地址：`http://127.0.0.1:8088`；模型状态接口：`http://127.0.0.1:8001/api/model/status`。三种模式共用后端工具注册表及只读统计、点位、Neo4j、Chroma 数据访问；模型仅选择工具和参数，统计数字由后端计算。

不要在有数据时执行 `docker compose down -v`，它会删除数据库、图谱、GeoServer 和向量索引数据。
