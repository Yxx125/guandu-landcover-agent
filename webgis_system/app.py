import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
from functools import lru_cache
from io import BytesIO
import colorsys
import json
import math
import os
import re
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import rasterize, shapes
from affine import Affine
from rasterio.transform import from_bounds
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform, reproject
from rasterio.windows import Window
from PIL import Image
from fastapi.responses import Response
from pydantic import BaseModel, Field
from config import (
    APP_ENV,
    CLCD_DIR,
    GEOSERVER_INTERNAL_URL,
    GEOSERVER_PUBLIC_URL,
    USE_LOCAL_OLLAMA,
    EMBEDDING_MODEL,
    database_connect_kwargs,
)


GEOSERVER_WMS = f"{GEOSERVER_INTERNAL_URL}/guandu_clcd/wms"
GEOSERVER_PUBLIC_WMS = f"{GEOSERVER_PUBLIC_URL}/guandu_clcd/wms"
FIRST_YEAR = 1990
LAST_YEAR = 2025
CLASS_NAMES = {
    1: "耕地", 2: "林地", 3: "灌丛", 4: "草地", 5: "水体",
    6: "积雪／冰川", 7: "裸地", 8: "建设用地（不透水面）", 9: "湿地",
}

app = FastAPI(
    title="官渡区土地覆盖变化查询系统",
    description="1990–2025 年土地覆盖变化、统计与知识图谱问答接口",
    version="1.1.0",
)

# 允许本机网页向 FastAPI 发起请求。
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


@app.get("/api/map/colors")
@lru_cache(maxsize=36)
def map_class_colors(year: int = Query(..., ge=FIRST_YEAR, le=LAST_YEAR)):
    """取当前年度 GeoServer 默认栅格样式的真实颜色，供地图图例和图表共用。"""
    from map_palette import extract_legend_colors

    params = urlencode({"SERVICE": "WMS", "VERSION": "1.1.1",
                        "REQUEST": "GetLegendGraphic", "FORMAT": "image/png",
                        "LAYER": f"guandu_clcd:guandu_{year}_3857", "WIDTH": 18,
                        "HEIGHT": 18, "LEGEND_OPTIONS": "forceRule:true;fontSize:13"})
    try:
        with urlopen(f"{GEOSERVER_WMS}?{params}", timeout=15) as response:
            png = response.read(1_000_001)
        if len(png) > 1_000_000:
            raise ValueError("GeoServer 图例超过预期大小")
        colors = extract_legend_colors(png)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        # GeoServer legend rendering is optional for the UI; use the
        # authoritative database palette so statistics and map controls remain
        # usable while the WMS style is being repaired.
        print(f"GeoServer 地类配色读取失败，回退数据库配色：{exc}")
        colors = ["#E8C85A", "#3A995B", "#8DB35A", "#C4D86A", "#4B9BD3",
                  "#E8F1F4", "#B8AA93", "#D9534F", "#59B7A4"]
        if len(colors) != len(CLASS_NAMES):
            raise HTTPException(503, "地图颜色图例未就绪，请检查 GeoServer 年度图层及样式") from exc
    return {"year": year, "classes": [
        {"class_code": code, "class_name": CLASS_NAMES[code], "color_hex": color}
        for code, color in enumerate(colors, start=1)
    ]}


@app.get("/health")
def health():
    return {
        "service": "ok",
        "environment": APP_ENV,
        "database": "可通过 /api/stats/annual?year=2025 验证",
        "geoserver": "地图图层已发布；此接口暂不检查连接",
        "knowledge_graph": "尚未连接",
    }


@app.get("/health/live")
def health_live():
    """进程存活检查，不访问外部依赖。"""
    return {"status": "ok", "service": "guandu-api"}


@app.get("/health/ready")
def health_ready():
    """生产就绪检查：数据库、年度栅格和 GeoServer 必须可用。"""
    checks = {}
    try:
        with psycopg.connect(**database_connect_kwargs()) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM guandu.annual_area")
                count = cursor.fetchone()[0]
        checks["database"] = {"ok": count >= 324, "annual_rows": count}
    except Exception as exc:
        checks["database"] = {"ok": False, "error": type(exc).__name__}

    raster_count = sum(1 for year in range(FIRST_YEAR, LAST_YEAR + 1)
                       if (CLCD_DIR / f"guandu_{year}.tif").is_file())
    checks["rasters"] = {"ok": raster_count == 36, "count": raster_count}

    try:
        url = (f"{GEOSERVER_INTERNAL_URL}/ows?service=WMS&version=1.1.1"
               "&request=GetCapabilities")
        with urlopen(url, timeout=5) as response:
            geoserver_ok = response.status == 200
        checks["geoserver"] = {"ok": geoserver_ok}
    except Exception as exc:
        checks["geoserver"] = {"ok": False, "error": type(exc).__name__}

    ready = all(item["ok"] for item in checks.values())
    if not ready:
        raise HTTPException(status_code=503, detail={"status": "not_ready", "checks": checks})
    return {"status": "ready", "checks": checks}


@app.get("/api/years")
def available_years():
    """返回已经制作了年度栅格的年份。"""
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
        raise HTTPException(status_code=400, detail="结束年份不能早于起始年份")

    years = list(range(start_year, final_year + 1))
    annual_layers = [
        {
            "year": year,
            "name": f"guandu_clcd:guandu_{year}_3857",
            "wms_url": GEOSERVER_PUBLIC_WMS,
            "tile_url": f"http://127.0.0.1:8001/api/tiles/annual/{year}/{{z}}/{{x}}/{{y}}.png",
            "preview_url": f"http://127.0.0.1:8001/api/maps/annual/{year}.png",
            "crs": "EPSG:3857",
        }
        for year in years
    ]

    change_layer = None
    if start_year < final_year:
        change_layer = {
            "type": "xyz",
            "tile_url": f"http://127.0.0.1:8001/api/tiles/changes/{start_year}/{final_year}/{{z}}/{{x}}/{{y}}.png",
            "crs": "EPSG:3857",
        }

    return {
        "start_year": start_year,
        "end_year": final_year,
        "years": years,
        "annual_layers": annual_layers,
        "change_layer": change_layer,
    }


@app.get("/api/stats/annual")
def annual_statistics(
    year: int = Query(..., ge=FIRST_YEAR, le=LAST_YEAR, description="统计年份"),
):
    """读取一年的九类面积、占比和配色；供柱状图和饼图使用。"""
    sql = """
        SELECT a.class_code, a.class_name, a.pixel_count,
               a.area_km2, a.percentage, c.color_hex
        FROM guandu.annual_area AS a
        JOIN guandu.land_class AS c ON c.class_code = a.class_code
        WHERE a.year = %s
        ORDER BY a.class_code
    """
    try:
        # 密码由启动服务的 PowerShell 环境变量 PGPASSWORD 提供。
        with psycopg.connect(
            **database_connect_kwargs(), row_factory=dict_row
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, (year,))
                rows = cursor.fetchall()
    except psycopg.Error as exc:
        # 服务端记录具体错误；接口不向浏览器泄露数据库密码或连接信息。
        print(f"读取年度统计失败：{exc}")
        raise HTTPException(status_code=503, detail="数据库连接或读取失败，请查看服务端终端") from exc

    if len(rows) != 9:
        raise HTTPException(
            status_code=503,
            detail=f"{year} 年应有 9 类统计记录，实际读到 {len(rows)} 条，请检查导入数据",
        )

    classes = [
        {
            "class_code": row["class_code"],
            "class_name": row["class_name"],
            "pixel_count": row["pixel_count"],
            "area_km2": float(row["area_km2"]),
            "percentage": float(row["percentage"]),
            "color_hex": row["color_hex"].strip(),
        }
        for row in rows
    ]
    return {
        "year": year,
        "unit": "km²",
        "total_area_km2": round(sum(item["area_km2"] for item in classes), 4),
        "classes": classes,
    }


@app.get("/api/stats/compare")
def compare_statistics(
    start_year: int = Query(..., ge=FIRST_YEAR, le=LAST_YEAR),
    end_year: int = Query(..., ge=FIRST_YEAR, le=LAST_YEAR),
):
    """返回两个年份各自的面积与占比，以及各地类面积差值。"""
    if end_year <= start_year:
        raise HTTPException(status_code=400, detail="结束年份必须晚于起始年份")

    first = annual_statistics(start_year)
    last = annual_statistics(end_year)
    last_by_code = {item["class_code"]: item for item in last["classes"]}

    changes = [
        {
            "class_code": item["class_code"],
            "class_name": item["class_name"],
            "color_hex": item["color_hex"],
            "start_area_km2": item["area_km2"],
            "end_area_km2": last_by_code[item["class_code"]]["area_km2"],
            "net_change_km2": round(
                last_by_code[item["class_code"]]["area_km2"] - item["area_km2"],
                4,
            ),
        }
        for item in first["classes"]
    ]

    return {
        "start_year": start_year,
        "end_year": end_year,
        "unit": "km²",
        "start": first,
        "end": last,
        "changes": changes,
    }


@app.get("/api/stats/timeseries")
def area_timeseries(
    start_year: int = Query(..., ge=FIRST_YEAR, le=LAST_YEAR),
    end_year: int = Query(..., ge=FIRST_YEAR, le=LAST_YEAR),
    class_codes: str = Query(..., description="地类编码，用英文逗号分隔，例如 1,2,8"),
):
    """返回选定地类逐年的面积序列，供柱状图和折线图使用。"""
    if end_year < start_year:
        raise HTTPException(status_code=400, detail="结束年份不能早于起始年份")

    parts = [part.strip() for part in class_codes.split(",")]
    if not parts or any(not part.isdigit() for part in parts):
        raise HTTPException(status_code=400, detail="class_codes 应为 1 至 9 的编码，以英文逗号分隔")
    codes = [int(part) for part in parts]
    if any(code not in range(1, 10) for code in codes) or len(set(codes)) != len(codes):
        raise HTTPException(status_code=400, detail="地类编码应为 1 至 9，不能重复")

    sql = """
        SELECT a.year, a.class_code, a.class_name, a.area_km2, c.color_hex
        FROM guandu.annual_area AS a
        JOIN guandu.land_class AS c ON c.class_code = a.class_code
        WHERE a.year BETWEEN %s AND %s AND a.class_code = ANY(%s)
        ORDER BY a.class_code, a.year
    """
    try:
        with psycopg.connect(
            **database_connect_kwargs(), row_factory=dict_row
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, (start_year, end_year, codes))
                rows = cursor.fetchall()
    except psycopg.Error as exc:
        print(f"读取时序统计失败：{exc}")
        raise HTTPException(status_code=503, detail="数据库连接或读取失败，请查看服务端终端") from exc

    years = list(range(start_year, end_year + 1))
    if len(rows) != len(years) * len(codes):
        raise HTTPException(status_code=503, detail="时序统计记录不完整，请检查年度面积表")

    by_code = {code: [] for code in codes}
    for row in rows:
        by_code[row["class_code"]].append(row)

    series = [
        {
            "class_code": code,
            "class_name": by_code[code][0]["class_name"],
            "color_hex": by_code[code][0]["color_hex"].strip(),
            "data": [
                {"year": row["year"], "area_km2": float(row["area_km2"])}
                for row in by_code[code]
            ],
        }
        for code in codes
    ]
    return {
        "start_year": start_year,
        "end_year": end_year,
        "years": years,
        "unit": "km²",
        "series": series,
    }


@app.get("/api/stats/net-change")
def yearly_net_change(
    start_year: int = Query(..., ge=FIRST_YEAR, le=LAST_YEAR),
    end_year: int = Query(..., ge=FIRST_YEAR, le=LAST_YEAR),
    class_code: int = Query(..., ge=1, le=9),
):
    """返回一个地类从起始年到结束年之间每年的面积净增减。"""
    if end_year <= start_year:
        raise HTTPException(status_code=400, detail="结束年份必须晚于起始年份")

    sql = """
        SELECT n.start_year, n.end_year, n.class_code, n.class_name,
               n.start_area_km2, n.end_area_km2, n.net_change_km2,
               c.color_hex
        FROM guandu.annual_net_change AS n
        JOIN guandu.land_class AS c ON c.class_code = n.class_code
        WHERE n.start_year >= %s AND n.end_year <= %s AND n.class_code = %s
        ORDER BY n.end_year
    """
    try:
        with psycopg.connect(
            **database_connect_kwargs(), row_factory=dict_row
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, (start_year, end_year, class_code))
                rows = cursor.fetchall()
    except psycopg.Error as exc:
        print(f"读取逐年净变化失败：{exc}")
        raise HTTPException(status_code=503, detail="数据库连接或读取失败，请查看服务端终端") from exc

    if len(rows) != end_year - start_year:
        raise HTTPException(status_code=503, detail="逐年净变化记录不完整，请检查数据库")

    data = [
        {
            "start_year": row["start_year"],
            "year": row["end_year"],
            "start_area_km2": float(row["start_area_km2"]),
            "end_area_km2": float(row["end_area_km2"]),
            "net_change_km2": float(row["net_change_km2"]),
        }
        for row in rows
    ]
    return {
        "start_year": start_year,
        "end_year": end_year,
        "class_code": class_code,
        "class_name": rows[0]["class_name"],
        "color_hex": rows[0]["color_hex"].strip(),
        "unit": "km²",
        "baseline_year": start_year,
        "data": data,
    }


@lru_cache(maxsize=32)
def _transition_rows_from_rasters(start_year: int, end_year: int):
    """对任意两年在原始一致网格上统计九类相互转移，缓存近期查询。"""
    first_tif = CLCD_DIR / f"guandu_{start_year}.tif"
    last_tif = CLCD_DIR / f"guandu_{end_year}.tif"
    if not first_tif.is_file() or not last_tif.is_file():
        raise HTTPException(status_code=503, detail="原始裁剪栅格文件缺失，无法计算转移矩阵")
    try:
        with rasterio.open(first_tif) as first, rasterio.open(last_tif) as last:
            if (first.crs is None or first.crs != last.crs
                    or first.transform != last.transform
                    or first.shape != last.shape):
                raise HTTPException(status_code=503, detail="两年栅格网格不一致，不能直接统计转移")
            start = first.read(1, masked=True)
            end = last.read(1, masked=True)
            first_mask = np.ma.getmaskarray(start)
            last_mask = np.ma.getmaskarray(end)
            if not np.array_equal(first_mask, last_mask):
                raise HTTPException(status_code=503, detail="两年栅格有效区域不一致，请先核对掩膜")
            valid = ~first_mask & (start.data >= 1) & (start.data <= 9)
            valid &= (end.data >= 1) & (end.data <= 9)
            if not np.any(valid):
                raise HTTPException(status_code=503, detail="两年栅格没有可统计的有效像元")
            pairs = start.data[valid].astype(np.int16) * 10 + end.data[valid].astype(np.int16)
            counts = np.bincount(pairs, minlength=100)
            pixel_area_km2 = abs(first.transform.a * first.transform.e
                                 - first.transform.b * first.transform.d) / 1_000_000
            if not 0.0008 <= pixel_area_km2 <= 0.0010:
                raise HTTPException(status_code=503, detail="栅格像元面积不符合预期的 30 米等面积网格")
            return [
                {
                    "from_code": from_code,
                    "from_name": CLASS_NAMES[from_code],
                    "to_code": to_code,
                    "to_name": CLASS_NAMES[to_code],
                    "pixel_count": int(counts[from_code * 10 + to_code]),
                    "area_km2": round(int(counts[from_code * 10 + to_code]) * pixel_area_km2, 4),
                }
                for from_code in range(1, 10)
                for to_code in range(1, 10)
            ]
    except (OSError, rasterio.errors.RasterioError, ValueError) as exc:
        print(f"动态转移矩阵计算失败：{exc}")
        raise HTTPException(status_code=503, detail="栅格读取或矩阵计算失败，请查看服务端终端") from exc


def _patch_pixels(geometry):
    """按像元坐标计算连通斑块面积；支持洞和八邻接的多面。"""
    def ring_area(ring):
        return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2)
                       in zip(ring, ring[1:]))) / 2

    polygons = ([geometry["coordinates"]] if geometry["type"] == "Polygon"
                else geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [])
    return round(sum(ring_area(poly[0]) - sum(ring_area(hole) for hole in poly[1:])
                     for poly in polygons))


@lru_cache(maxsize=8)
def _change_patch_mask(start_year: int, end_year: int, min_pixels: int):
    """在原始 30 米网格上筛选至少 min_pixels 个像元的八邻接变化斑块。"""
    first_tif = CLCD_DIR / f"guandu_{start_year}.tif"
    last_tif = CLCD_DIR / f"guandu_{end_year}.tif"
    if not first_tif.is_file() or not last_tif.is_file():
        raise HTTPException(status_code=503, detail="计算变化斑块所需的栅格不存在")
    try:
        with rasterio.open(first_tif) as first, rasterio.open(last_tif) as last:
            if first.crs is None or first.crs != last.crs or first.transform != last.transform or first.shape != last.shape:
                raise HTTPException(status_code=503, detail="年度栅格不对齐，不能筛选变化斑块")
            a = first.read(1, masked=True)
            b = last.read(1, masked=True)
            changed = ~np.ma.getmaskarray(a) & ~np.ma.getmaskarray(b)
            changed &= (a.data >= 1) & (a.data <= 9) & (b.data >= 1) & (b.data <= 9)
            changed &= a.data != b.data
            raw = changed.astype(np.uint8)
            if min_pixels > 1:
                # 对每个八邻接斑块独立计数后筛选。sieve 在二值图中
                # 可能把小的背景孔洞合并进变化区域，不能用它求严格斑块数。
                selected = [geometry for geometry, value in shapes(
                    raw, mask=changed, connectivity=8, transform=Affine.identity())
                    if value == 1 and _patch_pixels(geometry) >= min_pixels]
                raw = (rasterize(((geometry, 1) for geometry in selected),
                                 out_shape=raw.shape, transform=Affine.identity(),
                                 fill=0, dtype=np.uint8)
                       if selected else np.zeros_like(raw))
            return raw, first.transform, first.crs
    except (OSError, rasterio.errors.RasterioError, ValueError) as exc:
        print(f"变化斑块筛选失败：{exc}")
        raise HTTPException(status_code=503, detail="变化斑块筛选失败，请查看服务端终端") from exc


@lru_cache(maxsize=256)
def _change_tile(start_year: int, end_year: int, z: int, x: int, y: int,
                 selected: tuple[int, ...], min_pixels: int):
    first_tif = CLCD_DIR / f"guandu_{start_year}.tif"
    last_tif = CLCD_DIR / f"guandu_{end_year}.tif"
    if not first_tif.is_file() or not last_tif.is_file():
        raise HTTPException(status_code=503, detail="缺少生成变化图层所需的年度栅格")
    world = 40075016.68557849
    span = world / (2 ** z)
    west = -world / 2 + x * span
    north = world / 2 - y * span
    tile_grid = from_bounds(west, north - span, west + span, north, 256, 256)
    try:
        with rasterio.open(first_tif) as first, rasterio.open(last_tif) as last:
            if first.crs is None or first.crs != last.crs or first.transform != last.transform or first.shape != last.shape:
                raise HTTPException(status_code=503, detail="年度栅格网格不一致，无法生成变化图层")
            options = dict(crs="EPSG:3857", transform=tile_grid, width=256, height=256,
                           resampling=Resampling.nearest)
            with WarpedVRT(first, **options) as first_vrt, WarpedVRT(last, **options) as last_vrt:
                a = first_vrt.read(1, masked=True)
                b = last_vrt.read(1, masked=True)
        valid = ~np.ma.getmaskarray(a) & ~np.ma.getmaskarray(b)
        valid &= (a.data >= 1) & (a.data <= 9) & (b.data >= 1) & (b.data <= 9)
        valid &= a.data != b.data
        valid &= np.isin(a.data, selected) | np.isin(b.data, selected)
        if min_pixels > 1:
            patches, patch_transform, patch_crs = _change_patch_mask(
                start_year, end_year, min_pixels)
            tile_patches = np.zeros((256, 256), dtype=np.uint8)
            reproject(
                patches, tile_patches, src_transform=patch_transform,
                src_crs=patch_crs, src_nodata=0,
                dst_transform=tile_grid, dst_crs="EPSG:3857",
                dst_nodata=0, resampling=Resampling.nearest,
            )
            valid &= tile_patches == 1
        rgba = np.zeros((256, 256, 4), dtype=np.uint8)
        transitions = a.data.astype(np.int16) * 10 + b.data.astype(np.int16)
        # 色相由转入地类决定，源地类调整深浅；不变像元完全透明。
        target_hue = {1: .13, 2: .36, 3: .27, 4: .08, 5: .57,
                      6: .53, 7: .72, 8: .00, 9: .48}
        for code in np.unique(transitions[valid]):
            source_code, target_code = divmod(int(code), 10)
            red, green, blue = colorsys.hsv_to_rgb(
                (target_hue[target_code] + source_code * .009) % 1, .85, .95)
            match = valid & (transitions == code)
            rgba[match] = (int(red * 255), int(green * 255), int(blue * 255), 205)
        output = BytesIO()
        Image.fromarray(rgba, "RGBA").save(output, format="PNG")
        return output.getvalue()
    except (OSError, rasterio.errors.RasterioError, ValueError) as exc:
        print(f"变化图层瓦片生成失败：{exc}")
        raise HTTPException(status_code=503, detail="变化图层瓦片生成失败，请查看服务端终端") from exc


@app.get("/api/tiles/changes/{start_year}/{end_year}/{z}/{x}/{y}.png")
def change_tile(start_year: int, end_year: int, z: int, x: int, y: int,
                class_codes: str = Query("1,2,3,4,5,6,7,8,9"),
                min_patch_km2: float = Query(0, ge=0, le=100)):
    """按需生成任意年份组合的变化区域 XYZ 地图瓦片。"""
    if not FIRST_YEAR <= start_year < end_year <= LAST_YEAR:
        raise HTTPException(status_code=400, detail="起止年份必须在 1990–2025 且结束年份晚于起始年份")
    if not 0 <= z <= 19 or not 0 <= x < 2 ** z or not 0 <= y < 2 ** z:
        raise HTTPException(status_code=404, detail="地图瓦片坐标无效")
    parts = [part.strip() for part in class_codes.split(",")]
    if not parts or any(not part.isdigit() or int(part) not in CLASS_NAMES for part in parts):
        raise HTTPException(status_code=400, detail="class_codes 必须为 1–9，使用英文逗号分隔")
    selected = tuple(sorted(set(map(int, parts))))
    min_pixels = math.ceil(min_patch_km2 / .0009 - 1e-9)
    return Response(_change_tile(start_year, end_year, z, x, y, selected, min_pixels),
                    media_type="image/png", headers={"Cache-Control": "public, max-age=300"})


@lru_cache(maxsize=256)
def _annual_tile(year: int, z: int, x: int, y: int):
    tif = CLCD_DIR / f"guandu_{year}.tif"
    if not tif.is_file():
        raise HTTPException(status_code=404, detail="年度栅格不存在")
    world = 40075016.68557849
    span = world / (2 ** z)
    west, north = -world / 2 + x * span, world / 2 - y * span
    grid = from_bounds(west, north - span, west + span, north, 256, 256)
    try:
        with rasterio.open(tif) as src, WarpedVRT(src, crs="EPSG:3857", transform=grid,
                                                   width=256, height=256,
                                                   resampling=Resampling.nearest) as vrt:
            data = vrt.read(1, masked=True)
        rgba = np.zeros((256, 256, 4), dtype=np.uint8)
        palette = [(232,200,90),(58,153,91),(141,179,90),(196,216,106),
                   (75,155,211),(232,241,244),(184,170,147),(217,83,79),(89,183,164)]
        valid = ~np.ma.getmaskarray(data)
        for code, color in enumerate(palette, 1):
            rgba[valid & (data.data == code)] = (*color, 220)
        out = BytesIO(); Image.fromarray(rgba, "RGBA").save(out, format="PNG")
        return out.getvalue()
    except (OSError, rasterio.errors.RasterioError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="年度地图瓦片生成失败") from exc


@app.get("/api/tiles/annual/{year}/{z}/{x}/{y}.png")
def annual_tile(year: int, z: int, x: int, y: int):
    if not FIRST_YEAR <= year <= LAST_YEAR or not 0 <= z <= 19 or not 0 <= x < 2 ** z or not 0 <= y < 2 ** z:
        raise HTTPException(status_code=404, detail="地图瓦片坐标无效")
    return Response(_annual_tile(year, z, x, y), media_type="image/png",
                    headers={"Cache-Control": "public, max-age=300"})

@app.get("/api/maps/annual/{year}.png")
def annual_preview(year: int):
    tif = CLCD_DIR / f"guandu_{year}.tif"
    if not FIRST_YEAR <= year <= LAST_YEAR or not tif.is_file():
        raise HTTPException(status_code=404, detail="年度栅格不存在")
    with rasterio.open(tif) as src:
        data = src.read(1, out_shape=(240, 320), resampling=Resampling.nearest, masked=True)
    rgba = np.zeros((240, 320, 4), dtype=np.uint8)
    palette = [(232,200,90),(58,153,91),(141,179,90),(196,216,106),
               (75,155,211),(232,241,244),(184,170,147),(217,83,79),(89,183,164)]
    valid = ~np.ma.getmaskarray(data)
    for code, color in enumerate(palette, 1):
        rgba[valid & (data.data == code)] = (*color, 255)
    out = BytesIO(); Image.fromarray(rgba, "RGBA").save(out, format="PNG")
    return Response(out.getvalue(), media_type="image/png",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.get("/api/stats/transitions")
def transition_matrix(
    start_year: int = Query(..., ge=FIRST_YEAR, le=LAST_YEAR),
    end_year: int = Query(..., ge=FIRST_YEAR, le=LAST_YEAR),
):
    """返回已计算年份组合的九类转移矩阵和变化流向。"""
    if end_year <= start_year:
        raise HTTPException(status_code=400, detail="结束年份必须晚于起始年份")

    sql = """
        SELECT from_code, from_name, to_code, to_name, pixel_count, area_km2
        FROM guandu.transition_area
        WHERE start_year = %s AND end_year = %s
        ORDER BY from_code, to_code
    """
    try:
        with psycopg.connect(
            **database_connect_kwargs(), row_factory=dict_row
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, (start_year, end_year))
                rows = cursor.fetchall()
    except psycopg.Error as exc:
        print(f"读取转移矩阵失败：{exc}")
        raise HTTPException(status_code=503, detail="数据库连接或读取失败，请查看服务端终端") from exc

    if not rows:
        rows = _transition_rows_from_rasters(start_year, end_year)
    if len(rows) != 81:
        raise HTTPException(status_code=503, detail="转移矩阵应有 81 个格子，请检查导入数据")

    matrix = [
        {
            "from_code": row["from_code"],
            "from_name": row["from_name"],
            "to_code": row["to_code"],
            "to_name": row["to_name"],
            "pixel_count": row["pixel_count"],
            "area_km2": float(row["area_km2"]),
        }
        for row in rows
    ]
    # 对角线是地类未变化的区域，不作为桑基图的变化流向。
    flows = [
        item for item in matrix
        if item["from_code"] != item["to_code"] and item["pixel_count"] > 0
    ]
    return {
        "start_year": start_year,
        "end_year": end_year,
        "unit": "km²",
        "total_pixel_count": sum(item["pixel_count"] for item in matrix),
        "changed_pixel_count": sum(item["pixel_count"] for item in flows),
        "changed_area_km2": round(sum(item["area_km2"] for item in flows), 4),
        "matrix": matrix,
        "flows": flows,
    }


@app.get("/api/stats/changed-areas")
def changed_area_statistics(
    start_year: int = Query(..., ge=FIRST_YEAR, le=LAST_YEAR),
    end_year: int = Query(..., ge=FIRST_YEAR, le=LAST_YEAR),
    min_patch_km2: float = Query(0, ge=0, le=100),
):
    """统计变化像元转入转出；可按八邻接变化斑块最小面积筛选。"""
    if end_year <= start_year:
        raise HTTPException(status_code=400, detail="结束年份必须晚于起始年份")
    min_pixels = math.ceil(min_patch_km2 / .0009 - 1e-9)
    if min_pixels <= 1:
        result = transition_matrix(start_year, end_year)
        patch_count = None
    else:
        keep, _, _ = _change_patch_mask(start_year, end_year, min_pixels)
        patch_count = sum(
            1 for _, value in shapes(keep, mask=keep.astype(bool), connectivity=8)
            if value == 1
        )
        if patch_count * min_pixels > int(np.count_nonzero(keep)):
            raise HTTPException(503, "斑块数与筛选面积不一致，请检查斑块计算")
        with rasterio.open(CLCD_DIR / f"guandu_{start_year}.tif") as first:
            a = first.read(1)
        with rasterio.open(CLCD_DIR / f"guandu_{end_year}.tif") as last:
            b = last.read(1)
        pairs = a[keep == 1].astype(np.int16) * 10 + b[keep == 1].astype(np.int16)
        counts = np.bincount(pairs, minlength=100)
        flows = [
            {"from_code": i, "to_code": j, "pixel_count": int(counts[i * 10 + j])}
            for i in range(1, 10) for j in range(1, 10)
            if i != j and counts[i * 10 + j] > 0
        ]
        result = {"flows": flows, "changed_pixel_count": int(np.count_nonzero(keep))}

    by_class = {
        code: {
            "class_code": code,
            "class_name": CLASS_NAMES[code],
            "incoming_pixel_count": 0,
            "outgoing_pixel_count": 0,
        }
        for code in range(1, 10)
    }
    for flow in result["flows"]:
        by_class[flow["from_code"]]["outgoing_pixel_count"] += flow["pixel_count"]
        by_class[flow["to_code"]]["incoming_pixel_count"] += flow["pixel_count"]

    # 在原始 30 米等面积网格上，一个像元的面积是 0.0009 km²。
    # 用整数像元汇总后换算，可避免对已四舍五入的面积反复求和。
    classes = []
    for item in by_class.values():
        incoming = round(item["incoming_pixel_count"] * 0.0009, 4)
        outgoing = round(item["outgoing_pixel_count"] * 0.0009, 4)
        classes.append({
            **item,
            "incoming_area_km2": incoming,
            "outgoing_area_km2": outgoing,
            "net_change_km2": round(incoming - outgoing, 4),
        })

    return {
        "start_year": start_year,
        "end_year": end_year,
        "unit": "km²",
        "changed_pixel_count": result["changed_pixel_count"],
        "changed_area_km2": round(result["changed_pixel_count"] * 0.0009, 4),
        "min_patch_km2": min_patch_km2,
        "min_patch_pixels": min_pixels,
        "patch_count": patch_count,
        "classes": classes,
    }


@app.get("/api/point-history")
def point_history(
    lon: float = Query(..., ge=-180, le=180, description="地图点击位置的经度 EPSG:4326"),
    lat: float = Query(..., ge=-90, le=90, description="地图点击位置的纬度 EPSG:4326"),
    start_year: int = Query(..., ge=FIRST_YEAR, le=LAST_YEAR),
    end_year: int = Query(..., ge=FIRST_YEAR, le=LAST_YEAR),
):
    """查询官渡区原始等面积栅格中一个点的逐年地类与转换事件。"""
    if end_year < start_year:
        raise HTTPException(status_code=400, detail="结束年份不能早于起始年份")

    history = []
    try:
        for year in range(start_year, end_year + 1):
            tif = CLCD_DIR / f"guandu_{year}.tif"
            if not tif.is_file():
                raise HTTPException(status_code=503, detail=f"找不到 {year} 年原始裁剪栅格")
            with rasterio.open(tif) as source:
                if source.crs is None:
                    raise HTTPException(status_code=503, detail=f"{year} 年栅格缺少坐标系")
                xs, ys = transform("EPSG:4326", source.crs, [lon], [lat])
                row, col = source.index(xs[0], ys[0])
                if row < 0 or col < 0 or row >= source.height or col >= source.width:
                    raise HTTPException(status_code=404, detail="点位不在官渡区栅格范围内")
                pixel = source.read(1, window=Window(col, row, 1, 1), masked=True)
                if bool(np.ma.is_masked(pixel[0, 0])):
                    raise HTTPException(status_code=404, detail="点位落在官渡区边界之外或无数据区域")
                code = int(pixel[0, 0])
                if code not in CLASS_NAMES:
                    raise HTTPException(status_code=503, detail=f"{year} 年点位地类编码 {code} 不在 1–9 范围内")
                history.append({"year": year, "class_code": code, "class_name": CLASS_NAMES[code]})
    except (OSError, rasterio.errors.RasterioError, ValueError) as exc:
        print(f"点位栅格读取失败：{exc}")
        raise HTTPException(status_code=503, detail="栅格读取或坐标转换失败，请查看服务端终端") from exc

    changes = [
        {
            "from_year": earlier["year"],
            "to_year": later["year"],
            "from_code": earlier["class_code"],
            "from_name": earlier["class_name"],
            "to_code": later["class_code"],
            "to_name": later["class_name"],
        }
        for earlier, later in zip(history, history[1:])
        if earlier["class_code"] != later["class_code"]
    ]
    return {
        "lon": lon,
        "lat": lat,
        "start_year": start_year,
        "end_year": end_year,
        "history": history,
        "changes": changes,
        "change_count": len(changes),
    }


@app.get("/api/point-example")
def point_example():
    """从 2025 年栅格选择官渡区内的有效像元，方便验证点位接口。"""
    tif = CLCD_DIR / "guandu_2025.tif"
    if not tif.is_file():
        raise HTTPException(status_code=503, detail="找不到 2025 年原始裁剪栅格")
    try:
        with rasterio.open(tif) as source:
            if source.crs is None:
                raise HTTPException(status_code=503, detail="2025 年栅格缺少坐标系")
            pixels = source.read(1, masked=True)
            rows, cols = np.where(np.isin(pixels.filled(0), list(CLASS_NAMES)))
            if len(rows) == 0:
                raise HTTPException(status_code=503, detail="2025 年栅格中没有有效地类像元")
            middle = len(rows) // 2
            x, y = source.xy(int(rows[middle]), int(cols[middle]))
            lons, lats = transform(source.crs, "EPSG:4326", [x], [y])
            return {"lon": round(lons[0], 8), "lat": round(lats[0], 8), "year": 2025}
    except (OSError, rasterio.errors.RasterioError, ValueError) as exc:
        print(f"示例点位读取失败：{exc}")
        raise HTTPException(status_code=503, detail="栅格读取失败，请查看服务端终端") from exc


@app.get("/api/graph")
def graph_subgraph(
    start_year: int = Query(..., ge=FIRST_YEAR, le=LAST_YEAR),
    end_year: int = Query(..., ge=FIRST_YEAR, le=LAST_YEAR),
    class_codes: str = Query(..., description="地类编码，英文逗号分隔"),
):
    """从 Neo4j 读取所选地类的局部关系图。多年范围汇总逐年相邻转换。"""
    if end_year < start_year:
        raise HTTPException(status_code=400, detail="结束年份不能早于起始年份")
    parts = [part.strip() for part in class_codes.split(",")]
    if not parts or any(not part.isdigit() or int(part) not in CLASS_NAMES for part in parts):
        raise HTTPException(status_code=400, detail="class_codes 必须为 1–9，使用英文逗号分隔")
    codes = sorted(set(map(int, parts)))
    password = os.environ.get("NEO4J_PASSWORD")
    if not password:
        raise HTTPException(status_code=503, detail="FastAPI 进程未设置 NEO4J_PASSWORD")
    try:
        from neo4j import GraphDatabase
        from neo4j.exceptions import Neo4jError, ServiceUnavailable
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="虚拟环境尚未安装 neo4j Python 驱动") from exc
    try:
        uri = os.environ.get("NEO4J_URI", "bolt://127.0.0.1:7687")
        database = os.environ.get("NEO4J_DATABASE", "neo4j")
        with GraphDatabase.driver(
            uri, auth=(os.environ.get("NEO4J_USER", "neo4j"), password)
        ) as driver:
            driver.verify_connectivity()
            if start_year == end_year:
                records, _, _ = driver.execute_query(
                    """
                    MATCH (r:Region {name: '官渡区'})-[:HAS_OBSERVATION]->(o:Observation)
                          -[:OF_CLASS]->(c:LandClass)
                    WHERE o.year = $year AND c.code IN $codes
                    RETURN c.code AS code, c.name AS name,
                           o.area_km2 AS area_km2
                    ORDER BY code
                    """,
                    year=start_year, codes=codes, database_=database,
                )
                links = [
                    {"source": "region", "target": f"class-{row['code']}",
                     "value": float(row["area_km2"]), "label": "包含"}
                    for row in records
                ]
                nodes = [{"id": "region", "name": f"官渡区 {start_year}", "kind": "region"}]
                nodes.extend(
                    {"id": f"class-{row['code']}", "name": row["name"],
                     "kind": "class", "class_code": row["code"]}
                    for row in records
                )
                scope = "single_year_area"
            else:
                records, _, _ = driver.execute_query(
                    """
                    MATCH (a:Observation)-[t:TRANSITIONS_TO]->(b:Observation)
                    WHERE a.year >= $start AND b.year <= $end
                      AND (a.class_code IN $codes OR b.class_code IN $codes)
                    WITH a.class_code AS from_code, b.class_code AS to_code,
                         sum(t.pixel_count) AS pixels
                    MATCH (c1:LandClass {code: from_code})
                    MATCH (c2:LandClass {code: to_code})
                    RETURN from_code, c1.name AS from_name,
                           to_code, c2.name AS to_name, pixels
                    ORDER BY pixels DESC
                    """,
                    start=start_year, end=end_year, codes=codes, database_=database,
                )
                from_nodes = {row["from_code"]: row["from_name"] for row in records}
                to_nodes = {row["to_code"]: row["to_name"] for row in records}
                nodes = [
                    {"id": f"from-{code}", "name": f"{name}（转出）", "kind": "from",
                     "class_code": code}
                    for code, name in from_nodes.items()
                ] + [
                    {"id": f"to-{code}", "name": f"{name}（转入）", "kind": "to",
                     "class_code": code}
                    for code, name in to_nodes.items()
                ]
                links = [
                    {"source": f"from-{row['from_code']}",
                     "target": f"to-{row['to_code']}",
                     "value": round(row["pixels"] * .0009, 4),
                     "pixel_count": row["pixels"], "label": "逐年转为"}
                    for row in records
                ]
                scope = "adjacent_year_changes"
    except (Neo4jError, ServiceUnavailable, OSError, ValueError) as exc:
        print(f"Neo4j 图谱查询失败：{exc}")
        raise HTTPException(status_code=503, detail="Neo4j 连接或图谱查询失败，请查看服务端终端") from exc
    if not links:
        raise HTTPException(status_code=404, detail="图谱没有相应关系；请先运行知识图谱导入脚本")
    return {
        "start_year": start_year, "end_year": end_year,
        "scope": scope,
        "note": "多年图显示区间内相邻年份转换面积的累计，不等于起止两年的转移矩阵面积",
        "nodes": nodes, "links": links, "unit": "km²",
    }


@lru_cache(maxsize=1)
def _embedding_model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(EMBEDDING_MODEL)


def _retrieve_documents(question: str):
    """只查询已导入的真实文本；依赖或文档缺失时明确返回未配置状态。"""
    store = Path(__file__).resolve().parent / "data" / "chroma"
    if not store.is_dir():
        return [], "documents/ 尚未向量化入库"
    started = time.monotonic()
    def stage(name):
        print(f"文档检索阶段 {name}：{time.monotonic() - started:.1f}s", flush=True)
    try:
        import chromadb
        stage("导入 Chroma 完成")
        client = chromadb.PersistentClient(path=str(store))
        collection = client.get_collection("guandu_documents")
        count = collection.count()
        stage(f"打开集合完成，文本块 {count}")
        if count == 0:
            return [], "Chroma 集合为空"
        model = _embedding_model()
        stage("加载嵌入模型完成")
        vector = model.encode(
            [question], normalize_embeddings=True, show_progress_bar=False
        ).tolist()
        stage("问题向量编码完成")
        result = collection.query(
            query_embeddings=vector, n_results=min(3, count),
            include=["documents", "metadatas", "distances"]
        )
        stage("Chroma 相似度查询完成")
        evidence = [
            {"source": meta["source"], "chunk": meta["chunk"],
             "text": content, "distance": round(float(distance), 4)}
            for content, meta, distance in zip(
                result["documents"][0], result["metadatas"][0], result["distances"][0]
            )
        ]
        return evidence, "ok"
    except Exception as exc:
        print(f"Chroma 检索未就绪：{exc}")
        return [], "Chroma 或向量模型尚未就绪，请检查导入脚本和终端"


def _generate_from_evidence(question: str, factual_answer: str, graph, documents):
    """按 config.py 的唯一开关选择 Ollama 或兼容 Chat Completions 的云端服务。"""
    prompt = (
        "你只负责补充一句中文解释。确定性统计结论将由程序原样放在答案开头，"
        "因此不要复述、修改任何数字、年份、面积、百分比或单位，也不要使用阿拉伯数字。"
        "仅依据给出的图谱关系和检索文本解释，不得推断因果或编造事实。"
        "图谱两跳只是类别关系链，不能当作同一像元连续转化；"
        "相邻年图谱关系不能代替起止年直接叠置的转移矩阵。"
        "若证据不足，只回答：现有证据不足以进一步解释。\n"
        f"问题：{question}\n确定性统计结论：{factual_answer}\n"
        f"图谱关系：{json.dumps(graph['links'][:8], ensure_ascii=False) if graph else '未就绪'}\n"
        f"检索文本：{json.dumps(documents, ensure_ascii=False)[:5000]}"
    )
    if USE_LOCAL_OLLAMA:
        url = os.environ.get("OLLAMA_CHAT_URL", "http://127.0.0.1:11434/api/chat")
        payload = {"model": os.environ.get("OLLAMA_MODEL", "qwen2.5:7b"),
                   "messages": [{"role": "user", "content": prompt}], "stream": False}
        headers = {"Content-Type": "application/json"}
        mode = "ollama"
    else:
        base = os.environ.get("MODEL_API_BASE_URL", "").rstrip("/")
        key = os.environ.get("MODEL_API_KEY", "")
        if not base or not key:
            return factual_answer, "deterministic_statistics"
        url = base + "/chat/completions"
        payload = {"model": os.environ.get("MODEL_NAME", "gpt-5.6-sol"),
                   "messages": [{"role": "user", "content": prompt}]}
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
        mode = "cloud"
    try:
        request = Request(url, data=json.dumps(payload).encode("utf-8"),
                          headers=headers, method="POST")
        with urlopen(request, timeout=45) as response:
            data = json.load(response)
        generated = (data["message"]["content"] if mode == "ollama"
                     else data["choices"][0]["message"]["content"]).strip()
        # 统计结论始终直接来自本地计算，不让模型重述数值。
        # 生成文字出现数字时拒绝该文字，避免把未经校验的新数值带给用户。
        if not generated or re.search(r"\d", generated):
            print("模型解释含数字或为空，返回确定性统计结论")
            return factual_answer, "deterministic_fallback"
        return factual_answer + "\n" + generated, mode
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError) as exc:
        print(f"生成服务不可用，返回确定性统计回答：{exc}")
        return factual_answer, "deterministic_fallback"


class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=500)


@app.post("/api/ask")
def ask_question(request: AskRequest):
    """智能规划问答入口；模型仅选分析操作，数值由本地数据计算。"""
    from smart_query import answer_question
    return answer_question(request.question)


@app.get("/api/evidence")
def query_evidence(
    question: str = Query(..., min_length=3, max_length=500),
    start_year: int = Query(..., ge=FIRST_YEAR, le=LAST_YEAR),
    end_year: int = Query(..., ge=FIRST_YEAR, le=LAST_YEAR),
    class_codes: str = Query(...),
    source: str = Query("all", description="all、graph 或 documents"),
):
    """独立验收图谱及文档检索；不调用生成模型。"""
    if end_year < start_year:
        raise HTTPException(400, "结束年份不能早于起始年份")
    parts = [s.strip() for s in class_codes.split(",")]
    if not parts or any(not p.isdigit() or int(p) not in CLASS_NAMES for p in parts):
        raise HTTPException(400, "class_codes 必须为 1–9 的英文逗号分隔整数")
    if source not in {"all", "graph", "documents"}:
        raise HTTPException(400, "source 只能是 all、graph 或 documents")
    from graphrag_evidence import graph_paths
    paths, graph_status = (graph_paths(start_year, end_year, sorted(set(map(int, parts))))
                           if source in {"all", "graph"} else ([], "skipped"))
    documents, retrieval_status = (_retrieve_documents(question) if source in {"all", "documents"}
                                   else ([], "skipped"))
    return {"graph_paths": paths, "graph_status": graph_status,
            "documents": documents, "retrieval_status": retrieval_status,
            "note": "两跳类别关系不表示同一像元轨迹；文档检索片段不证明因果。"}


@app.post("/api/agent-demo/ask")
def agent_demo_ask(request: AskRequest):
    """独立的 Ollama 工具调用试验入口；原有 /api/ask 保持可用。"""
    from agent_runtime import answer_with_tools
    return answer_with_tools(request.question)


@app.post("/api/agent-full/ask")
def agent_full_ask(request: AskRequest):
    """统一查询入口：已核验工具和原有广覆盖统计规划器共同服务网页。"""
    from agent_query_router import answer_agent_query

    result = answer_agent_query(request.question)
    if "tool" not in result:
        # 原查询规划器已有完整的地图、图表、图谱联动条件。
        return result
    name = result["tool"]
    if name == "multi_tool":
        return result
    args = result["arguments"]
    if name == "compare_transition_periods":
        result["generation_mode"] = "agent_multi_tool"
        result["evidence"] = {"documents": [], "retrieval_status": "not_needed",
                              "statistics": result["tool_result"]["source"]}
        return result
    start = args.get("year", args.get("start_year"))
    end = args.get("year", args.get("end_year"))
    classes = args.get("class_codes")
    if classes is None:
        classes = [args["class_code"]] if "class_code" in args else []
    if name == "get_annual_area":
        # 年度面积工具返回九类；网页上只勾选提问时明确出现的地类。
        aliases = {8: ("建设用地", "不透水面"), 5: ("水体", "水域")}
        classes = [code for code, label in CLASS_NAMES.items()
                   if label in request.question or
                   any(word in request.question for word in aliases.get(code, ()))]
    if name == "get_transition_matrix":
        classes = [code for code, label in CLASS_NAMES.items()
                   if label in request.question or
                   (code == 8 and "建设用地" in request.question)]
    if not classes:
        classes = list(range(1, 10))
    mode = {
        "get_annual_area": "annual",
        "get_transition_matrix": "transition",
        "compare_years": "compare",
        "get_area_timeseries": "timeseries",
        "get_yearly_net_change": "netchange",
        "get_changed_areas": "changedareas",
        "get_point_history": "timeseries",
        "get_knowledge_graph": "annual" if start == end else "timeseries",
    }[name]
    result["query"] = {
        "start_year": start, "end_year": end,
        "class_codes": classes, "chart_mode": mode,
        "min_patch_km2": args.get("min_patch_km2", 0),
    }
    from model_gateway import mode_name
    result["generation_mode"] = "agent_full_" + mode_name()
    result["evidence"] = {"documents": [], "retrieval_status": "not_needed",
                          "statistics": result["tool_result"]["source"]}
    return result
