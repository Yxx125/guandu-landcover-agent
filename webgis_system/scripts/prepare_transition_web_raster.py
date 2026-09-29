"""将变化类型栅格对齐到已发布的年度网页地图网格。"""

import argparse
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import Resampling, reproject


CHANGE_DIR = Path(r"F:\官渡区\3_output\change")
WEB_DIR = Path(r"F:\官渡区\3_output\web_data")


def prepare(start_year, end_year):
    source_path = (
        CHANGE_DIR
        / f"transition_type_{start_year}_{end_year}.tif"
    )
    reference_path = (
        WEB_DIR
        / f"guandu_{end_year}_3857.tif"
    )
    output_path = (
        WEB_DIR
        / f"transition_type_{start_year}_{end_year}_3857.tif"
    )

    for path in (source_path, reference_path):
        if not path.is_file():
            raise FileNotFoundError(f"找不到文件：{path}")

    if output_path.exists():
        raise FileExistsError(
            f"输出文件已存在，未覆盖：{output_path}"
        )

    with rasterio.open(source_path) as source, rasterio.open(
        reference_path
    ) as reference:
        if reference.crs.to_epsg() != 3857:
            raise ValueError(
                f"参考栅格不是 EPSG:3857：{reference.crs}"
            )

        result = np.zeros(
            (reference.height, reference.width),
            dtype=np.uint8,
        )

        reproject(
            source=rasterio.band(source, 1),
            destination=result,
            src_transform=source.transform,
            src_crs=source.crs,
            src_nodata=0,
            dst_transform=reference.transform,
            dst_crs=reference.crs,
            dst_nodata=0,
            resampling=Resampling.nearest,
        )

        profile = reference.profile.copy()
        profile.update(
            driver="GTiff",
            dtype="uint8",
            count=1,
            nodata=0,
            compress="lzw",
        )

        with rasterio.open(output_path, "w", **profile) as dst:
            dst.write(result, 1)

    print(f"输出文件：{output_path}")
    print("坐标系：EPSG:3857")
    print("网格：与结束年份的网页栅格一致")
    print(f"网页栅格中的变化像元：{np.count_nonzero(result)}")
    print(
        "网页栅格中的耕地→建设用地像元："
        f"{np.count_nonzero(result == 18)}"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("start_year", type=int)
    parser.add_argument("end_year", type=int)
    args = parser.parse_args()

    if not (1990 <= args.start_year < args.end_year <= 2025):
        parser.error(
            "年份须满足 1990 ≤ 起始年份 < 结束年份 ≤ 2025"
        )

    prepare(args.start_year, args.end_year)


if __name__ == "__main__":
    main()