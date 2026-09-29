# 官渡区土地覆盖变化查询系统

这是一个 1990-2025 年官渡区土地覆盖变化分析与 WebGIS 查询系统。

## 两种运行方式

Docker 不是运行本项目的强制条件。它只负责把 PostgreSQL、GeoServer、Neo4j、FastAPI 和前端代理统一放进可重复的运行环境。服务器部署优先使用 Docker；已有 Windows GIS 环境时，可以继续使用手工模式。

### Windows 手工模式

先启动 PostgreSQL、GeoServer、Neo4j。然后进入 `webgis_system`：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8001
```

另开终端启动前端：

```powershell
& "F:\官渡区\webgis_system\.venv\Scripts\python.exe" -m http.server 5173 --directory "F:\官渡区\webgis_system\web"
```

浏览器访问 `http://127.0.0.1:5173`。如果前端需要访问非同源后端，在地址后增加 `?api=http://127.0.0.1:8001`。

### Docker Compose 模式

复制 `.env.example` 为 `.env`，修改密码和模型配置：

```powershell
Copy-Item .env.example .env
.\start.ps1
```

默认访问 `http://127.0.0.1:8088`。脚本会初始化数据库、GeoServer 图层、知识图谱、文档索引和 API。

## 依赖和验证

Python 依赖在 `webgis_system/requirements.txt`。核心验收：

```powershell
cd webgis_system
.\.venv\Scripts\python.exe .\scripts\10_acceptance.py --skip-llm
.\.venv\Scripts\python.exe .\scripts\12_query_regression.py
```

生产部署细节见 `DEPLOYMENT.md`。
