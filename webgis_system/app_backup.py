from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware


GEOSERVER_WMS = "http://localhost:8080/geoserver/guandu_clcd/wms"
FIRST_YEAR = 1990
LAST_YEAR = 2025

app = FastAPI(
    title="官渡区土地覆盖变化查询系统",
    description="1990–2025 年土地覆盖变化、统计与知识图谱问答接口",
    version="0.2.0",
)

# 保留现有的本机网页访问设置。
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5500",
        "http://127.0.0.1:5500",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/")
def home():
    return {
        "project": "官渡区 1990–2025 年土地覆盖变化查询系统",
        "message": "FastAPI 基础服务已启动",
    }


@app.get("/health")
def health():
    return {
        "service": "ok",
        "database": "尚未连接",
        "geoserver": "地图图层已发布；此接口暂不检查连接",
        "knowledge_graph": "尚未连接",
    }


@app.get("/api/years")
def available_years():
    """告诉网页哪些年份有年度地图。"""
    return {
        "first_year": FIRST_YEAR,
        "last_year": LAST_YEAR,
        "years": list(range(FIRST_YEAR, LAST_YEAR + 1)),
    }


@app.get("/api/maps")
def query_maps(
    start_year: int = Query(
        ...,
        ge=FIRST_YEAR,
        le=LAST_YEAR,
        description="起始年份；单年查询也填写此参数",
    ),
    end_year: int | None = Query(
        None,
        ge=FIRST_YEAR,
        le=LAST_YEAR,
        description="结束年份；单年查询可不填写",
    ),
):
    """返回查询年份对应的 GeoServer WMS 图层信息。"""
    final_year = start_year if end_year is None else end_year

    if final_year < start_year:
        raise HTTPException(
            status_code=400,
            detail="结束年份不能早于起始年份",
        )

    years = list(range(start_year, final_year + 1))

    annual_layers = [
        {
            "year": year,
            "name": f"guandu_clcd:guandu_{year}_3857",
            "wms_url": GEOSERVER_WMS,
            "crs": "EPSG:3857",
        }
        for year in years
    ]

    # 目前只发布并核验了 2010→2020 的变化高亮图层。
    change_layer = None
    if start_year == 2010 and final_year == 2020:
        change_layer = {
            "name": (
                "guandu_clcd:"
                "transition_type_2010_2020_3857"
            ),
            "wms_url": GEOSERVER_WMS,
            "crs": "EPSG:3857",
            "style": "guandu_clcd:transition_types",
        }

    return {
        "start_year": start_year,
        "end_year": final_year,
        "years": years,
        "annual_layers": annual_layers,
        "change_layer": change_layer,
    }