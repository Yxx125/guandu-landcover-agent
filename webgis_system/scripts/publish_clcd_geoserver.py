"""将官渡区 CLCD GeoTIFF 上传到 GeoServer，并设置统一地类样式。"""

import argparse
import base64
import getpass
import http.client
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


BASE_URL = "http://localhost:8080/geoserver/rest"
WORKSPACE = "guandu_clcd"
STYLE = "clcd_9classes"
RASTER_DIR = Path(r"F:\官渡区\3_output\web_data")


def authorization_header(username, password):
    """生成 GeoServer 登录认证信息。"""
    credentials = f"{username}:{password}".encode("utf-8")
    encoded = base64.b64encode(credentials).decode("ascii")
    return f"Basic {encoded}"


def request(method, endpoint, username, password, body=None, content_type=None):
    """发送普通的 GeoServer REST 请求。"""
    headers = {
        "Authorization": authorization_header(username, password),
        "Accept": "application/json",
    }
    if content_type:
        headers["Content-Type"] = content_type

    req = Request(
        f"{BASE_URL}{endpoint}",
        data=body,
        headers=headers,
        method=method,
    )

    try:
        with urlopen(req, timeout=60) as response:
            return response.status, response.read().decode(
                "utf-8", errors="replace"
            )
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except URLError as exc:
        raise RuntimeError(f"无法连接 GeoServer：{exc.reason}") from exc


def upload_geotiff(endpoint, username, password, tif_path):
    """把 TIFF 文件内容上传给 GeoServer，不要求它读取本机文件路径。"""
    connection = http.client.HTTPConnection(
        "localhost", 8080, timeout=300
    )

    try:
        with tif_path.open("rb") as tif_file:
            connection.request(
                "PUT",
                f"/geoserver/rest{endpoint}",
                body=tif_file,
                headers={
                    "Authorization": authorization_header(
                        username, password
                    ),
                    "Content-Type": "image/tiff",
                    "Content-Length": str(tif_path.stat().st_size),
                },
            )

            response = connection.getresponse()
            response_text = response.read().decode(
                "utf-8", errors="replace"
            )
            return response.status, response_text
    except (OSError, http.client.HTTPException) as exc:
        raise RuntimeError(f"上传文件时出错：{exc}") from exc
    finally:
        connection.close()


def require_success(status, response, operation):
    """HTTP 状态码不是成功状态时，显示具体错误。"""
    if not 200 <= status < 300:
        raise RuntimeError(
            f"{operation}失败：HTTP {status}\n"
            f"{response[:1500]}"
        )


def publish_year(year, username, password):
    """发布一个年份的栅格图层。"""
    layer_name = f"guandu_{year}_3857"
    store_name = f"clcd_guandu_{year}"
    qualified_layer = f"{WORKSPACE}:{layer_name}"
    tif_path = RASTER_DIR / f"{layer_name}.tif"

    layer_endpoint = (
        f"/layers/{quote(qualified_layer, safe=':')}.json"
    )
    store_endpoint = (
        f"/workspaces/{WORKSPACE}/coveragestores/"
        f"{store_name}.json"
    )

    # 已有图层直接跳过，避免重复发布。
    status, response = request(
        "GET", layer_endpoint, username, password
    )
    if status == 200:
        print(f"[跳过] {year}：图层已存在")
        return
    if status != 404:
        require_success(status, response, f"检查 {year} 年图层")

    # 存储存在但图层不存在时停止，避免覆盖已有配置。
    status, response = request(
        "GET", store_endpoint, username, password
    )
    if status == 200:
        raise RuntimeError(
            f"{year} 年数据存储已存在，但图层不存在。"
            "请先检查 GeoServer 中该存储的状态。"
        )
    if status != 404:
        require_success(status, response, f"检查 {year} 年数据存储")

    # 直接上传 TIFF 文件，而不是让 GeoServer 查找 F: 盘路径。
    upload_endpoint = (
        f"/workspaces/{WORKSPACE}/coveragestores/"
        f"{store_name}/file.geotiff?configure=first"
        f"&coverageName={layer_name}"
    )
    status, response = upload_geotiff(
        upload_endpoint, username, password, tif_path
    )
    require_success(
        status, response, f"上传并发布 {year} 年栅格"
    )

    # 设置九类土地覆盖颜色样式。
    style_xml = (
        "<layer><defaultStyle>"
        f"<name>{STYLE}</name>"
        f"<workspace>{WORKSPACE}</workspace>"
        "</defaultStyle></layer>"
    ).encode("utf-8")

    status, response = request(
        "PUT",
        f"/layers/{quote(qualified_layer, safe=':')}.xml",
        username,
        password,
        body=style_xml,
        content_type="application/xml; charset=UTF-8",
    )
    require_success(
        status, response, f"设置 {year} 年默认样式"
    )

    # 查询 GeoServer，确认图层和样式确实存在。
    status, response = request(
        "GET", layer_endpoint, username, password
    )
    require_success(
        status, response, f"核验 {year} 年图层"
    )

    layer = json.loads(response)["layer"]
    actual_style = layer.get(
        "defaultStyle", {}
    ).get("name", "")

    if actual_style.split(":")[-1] != STYLE:
        raise RuntimeError(
            f"{year} 年图层已发布，但默认样式为 "
            f"{actual_style!r}，不是 {STYLE!r}"
        )

    print(
        f"[成功] {year}：{qualified_layer}，"
        f"默认样式 {actual_style}"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--all",
        action="store_true",
        help="处理 1991–2025 年；不加时只测试 1991 年",
    )
    args = parser.parse_args()

    years = (
        list(range(1991, 2026))
        if args.all
        else [1991]
    )

    missing = [
        str(RASTER_DIR / f"guandu_{year}_3857.tif")
        for year in years
        if not (
            RASTER_DIR / f"guandu_{year}_3857.tif"
        ).is_file()
    ]
    if missing:
        raise SystemExit(
            "缺少以下文件：\n" + "\n".join(missing)
        )

    username = (
        input("GeoServer 用户名 [admin]：").strip()
        or "admin"
    )
    password = getpass.getpass(
        "GeoServer 密码（输入时不显示）："
    )
    if not password:
        raise SystemExit("未输入密码，操作已停止。")

    for year in years:
        try:
            publish_year(year, username, password)
        except (
            RuntimeError,
            ValueError,
            OSError,
        ) as exc:
            raise SystemExit(
                f"\n处理到 {year} 年时停止：\n{exc}\n"
                "已成功发布的年份会保留。"
                "解决问题后可重新运行，"
                "脚本会跳过已有图层。"
            ) from exc

    print("本次处理完成。")


if __name__ == "__main__":
    main()