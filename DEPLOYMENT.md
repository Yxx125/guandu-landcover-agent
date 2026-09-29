# 官渡区土地覆盖系统部署说明

复制 `.env.example` 为 `.env`，修改密码和模型配置后执行 `start.ps1`（Linux 执行 `start.sh`）。默认地址是 `http://127.0.0.1:8088`。

启动脚本会初始化 PostgreSQL、GeoServer、Neo4j、Chroma、FastAPI 和 Nginx。部署后执行 `webgis_system/scripts/10_acceptance.py` 与 `12_query_regression.py`，并在浏览器检查地图、图例、图表和问答联动。

本地模型可用 `docker compose --profile local-model up -d ollama` 启动，再执行 `docker compose exec ollama ollama pull qwen2.5:7b`，并将 `USE_LOCAL_OLLAMA=true` 写入 `.env`。

不要在有数据时执行 `docker compose down -v`，它会删除数据库、图谱、GeoServer 和向量索引数据。
