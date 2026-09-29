"""计算官渡区任意两个年份的 CLCD 地类转移矩阵，输出 CSV。"""

import argparse
import csv
from pathlib import Path

import numpy as np
import rasterio


RASTER_DIR = Path(r"F:\官渡区\3_output\clcd_guandu")
OUTPUT_DIR = Path(r"F:\官渡区\3_output\change")

CLASS_NAMES = {
    1: "耕地",
    2: "林地",
    3: "灌丛",
    4: "草地",
    5: "水体",
    6: "积雪／冰川",
    7: "裸地",
    8: "建设用地（不透水面）",
    9: "湿地",
}


def calculate(start_year, end_year):
    start_path = RASTER_DIR / f"guandu_{start_year}.tif"
    end_path = RASTER_DIR / f"guandu_{end_year}.tif"

    for path in (start_path, end_path):
        if not path.is_file():
            raise FileNotFoundError(f"找不到栅格：{path}")

    with rasterio.open(start_path) as start_src, rasterio.open(
        end_path
    ) as end_src:
        start_grid = (
            start_src.crs,
            start_src.width,
            start_src.height,
            start_src.transform,
        )
        end_grid = (
            end_src.crs,
            end_src.width,
            end_src.height,
            end_src.transform,
        )
        if start_grid != end_grid:
            raise ValueError("两个年份的坐标系、尺寸或网格原点不一致")

        start = start_src.read(1, masked=True)
        end = end_src.read(1, masked=True)

        valid = (
            ~np.ma.getmaskarray(start)
            & ~np.ma.getmaskarray(end)
            & (start.data >= 1)
            & (start.data <= 9)
            & (end.data >= 1)
            & (end.data <= 9)
        )

        valid_pixels = int(np.count_nonzero(valid))
        if valid_pixels != 704187:
            raise ValueError(
                f"共同有效像元为 {valid_pixels}，预期为 704187；"
                "请先检查原始栅格"
            )

        # 使用实际网格变换计算单个像元面积，单位为平方千米。
        transform = start_src.transform
        pixel_area_km2 = abs(
            transform.a * transform.e
            - transform.b * transform.d
        ) / 1_000_000

        # 1→1 至 9→9，共 81 种组合；包括未发生变化的像元。
        pair_codes = (
            (start.data[valid].astype(np.int16) - 1) * 9
            + (end.data[valid].astype(np.int16) - 1)
        )
        counts = np.bincount(pair_codes, minlength=81)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = (
        OUTPUT_DIR
        / f"transition_{start_year}_{end_year}.csv"
    )

    if output_path.exists():
        raise FileExistsError(
            f"输出文件已存在，脚本没有覆盖它：{output_path}"
        )

    with output_path.open(
        "w", newline="", encoding="utf-8-sig"
    ) as file:
        writer = csv.writer(file)
        writer.writerow(
            [
                "start_year",
                "end_year",
                "from_code",
                "from_name",
                "to_code",
                "to_name",
                "pixel_count",
                "area_km2",
            ]
        )

        for from_code in range(1, 10):
            for to_code in range(1, 10):
                index = (from_code - 1) * 9 + (to_code - 1)
                count = int(counts[index])
                writer.writerow(
                    [
                        start_year,
                        end_year,
                        from_code,
                        CLASS_NAMES[from_code],
                        to_code,
                        CLASS_NAMES[to_code],
                        count,
                        f"{count * pixel_area_km2:.4f}",
                    ]
                )

    print(f"起止年份：{start_year} → {end_year}")
    print(f"有效像元：{valid_pixels}")
    print(f"像元面积：{pixel_area_km2:.6f} 平方千米")
    print(f"矩阵记录：{len(counts)} 行")
    print(f"输出文件：{output_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("start_year", type=int)
    parser.add_argument("end_year", type=int)
    args = parser.parse_args()

    if not (1990 <= args.start_year < args.end_year <= 2025):
        parser.error("年份须满足 1990 ≤ 起始年份 < 结束年份 ≤ 2025")

    calculate(args.start_year, args.end_year)


if __name__ == "__main__":
    main()