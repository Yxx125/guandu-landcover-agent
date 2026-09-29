"""上传一张变化类型栅格到 GeoServer，并设置默认样式。"""

import argparse
import base64
import getpass
import http.client
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


HOST = "localhost"
PORT = 8080
WORKSPACE = "guandu_clcd"
STYLE = "transition_types"
WEB_DIR = Path(r"F:\官渡区\3_output\web_data")


def auth_header(username, password):
    credentials = f"{username}:{password}".encode("utf-8")
    encoded = base64.b64encode(credentials).decode("ascii")
    return f"Basic {encoded}"


def api_request(method, endpoint, username, password, body=None):
    headers = {
        "Authorization": auth_header(username, password),
        "Accept": "application/json",
    }
    if body is not None:
        headers["Content-Type"] = "application/xml; charset=UTF-8"

    request = Request(
        f"http://{HOST}:{PORT}/geoserver/rest{endpoint}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urlopen(request, timeout=60) as response:
            return response.status, response.read().decode(
                "utf-8", errors="replace"
            )
    except HTTPError as exc:
        return exc.code, exc.read().decode(
            "utf-8", errors="replace"
        )
    except URLError as exc:
        raise RuntimeError(
            f"无法连接 GeoServer：{exc.reason}"
        ) from exc


def upload_tiff(endpoint, username, password, path):
    connection = http.client.HTTPConnection(
        HOST, PORT, timeout=300
    )
    try:
        with path.open("rb") as file:
            connection.request(
                "PUT",
                f"/geoserver/rest{endpoint}",
                body=file,
                headers={
                    "Authorization": auth_header(
                        username, password
                    ),
                    "Content-Type": "image/tiff",
                    "Content-Length": str(path.stat().st_size),
                },
            )
            response = connection.getresponse()
            return response.status, response.read().decode(
                "utf-8", errors="replace"
            )
    finally:
        connection.close()


def require_success(status, response, operation):
    if not 200 <= status < 300:
        raise RuntimeError(
            f"{operation}失败：HTTP {status}\n"
            f"{response[:1500]}"
        )


def publish(start_year, end_year, username, password):
    layer_name = (
        f"transition_type_{start_year}_{end_year}_3857"
    )
    store_name = (
        f"transition_{start_year}_{end_year}"
    )
    qualified_name = f"{WORKSPACE}:{layer_name}"
    tif_path = WEB_DIR / f"{layer_name}.tif"

    if not tif_path.is_file():
        raise FileNotFoundError(f"找不到文件：{tif_path}")

    layer_endpoint = f"/layers/{qualified_name}.json"
    store_endpoint = (
        f"/workspaces/{WORKSPACE}/coveragestores/"
        f"{store_name}.json"
    )

    status, response = api_request(
        "GET", layer_endpoint, username, password
    )
    if status == 200:
        raise RuntimeError(
            f"图层 {qualified_name} 已存在；"
            "脚本没有覆盖已有图层"
        )
    if status != 404:
        require_success(status, response, "检查图层")

    status, response = api_request(
        "GET", store_endpoint, username, password
    )
    if status == 200:
        raise RuntimeError(
            f"存储 {store_name} 已存在；"
            "脚本没有覆盖已有存储"
        )
    if status != 404:
        require_success(status, response, "检查存储")

    upload_endpoint = (
        f"/workspaces/{WORKSPACE}/coveragestores/"
        f"{store_name}/file.geotiff"
        f"?configure=first&coverageName={layer_name}"
    )
    status, response = upload_tiff(
        upload_endpoint, username, password, tif_path
    )
    require_success(
        status, response, "上传变化类型栅格"
    )
    print(f"[成功] 已上传图层：{qualified_name}")

    style_xml = (
        "<layer><defaultStyle>"
        f"<name>{STYLE}</name>"
        f"<workspace>{WORKSPACE}</workspace>"
        "</defaultStyle></layer>"
    ).encode("utf-8")

    status, response = api_request(
        "PUT",
        f"/layers/{qualified_name}.xml",
        username,
        password,
        body=style_xml,
    )
    require_success(
        status, response, "设置默认样式"
    )

    status, response = api_request(
        "GET", layer_endpoint, username, password
    )
    require_success(status, response, "核验图层")

    layer = json.loads(response)["layer"]
    actual_style = layer.get(
        "defaultStyle", {}
    ).get("name", "")
    if actual_style.split(":")[-1] != STYLE:
        raise RuntimeError(
            f"图层已发布，但默认样式是 "
            f"{actual_style!r}，预期为 {STYLE!r}"
        )

    print(f"[成功] 默认样式：{actual_style}")
    print("发布完成。")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("start_year", type=int)
    parser.add_argument("end_year", type=int)
    args = parser.parse_args()

    if not (1990 <= args.start_year < args.end_year <= 2025):
        parser.error(
            "年份须满足 1990 ≤ 起始年份 < 结束年份 ≤ 2025"
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

    try:
        publish(
            args.start_year,
            args.end_year,
            username,
            password,
        )
    except (RuntimeError, OSError, ValueError) as exc:
        raise SystemExit(f"发布停止：{exc}") from exc


if __name__ == "__main__":
    main()