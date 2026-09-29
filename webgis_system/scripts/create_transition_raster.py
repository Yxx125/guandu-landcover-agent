"""生成两个年份之间的土地覆盖转移类型栅格。"""

import argparse
from pathlib import Path

import numpy as np
import rasterio


RASTER_DIR = Path(r"F:\官渡区\3_output\clcd_guandu")
OUTPUT_DIR = Path(r"F:\官渡区\3_output\change")


def create_raster(start_year, end_year):
    start_path = RASTER_DIR / f"guandu_{start_year}.tif"
    end_path = RASTER_DIR / f"guandu_{end_year}.tif"
    output_path = (
        OUTPUT_DIR
        / f"transition_type_{start_year}_{end_year}.tif"
    )

    for path in (start_path, end_path):
        if not path.is_file():
            raise FileNotFoundError(f"找不到输入文件：{path}")

    if output_path.exists():
        raise FileExistsError(
            f"输出文件已存在，未覆盖：{output_path}"
        )

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

        valid_count = int(np.count_nonzero(valid))
        if valid_count != 704187:
            raise ValueError(
                f"共同有效像元为 {valid_count}，"
                "与已核对的 704187 不一致"
            )

        changed = valid & (start.data != end.data)

        # 0 表示区外或未变化；例如 18 表示耕地→建设用地。
        result = np.zeros(start.shape, dtype=np.uint8)
        result[changed] = (
            start.data[changed].astype(np.uint8) * 10
            + end.data[changed].astype(np.uint8)
        )

        profile = start_src.profile.copy()
        profile.update(
            driver="GTiff",
            dtype="uint8",
            count=1,
            nodata=0,
            compress="lzw",
        )

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        with rasterio.open(output_path, "w", **profile) as dst:
            dst.write(result, 1)

    print(f"起止年份：{start_year} → {end_year}")
    print(f"共同有效像元：{valid_count}")
    print(f"发生地类变化：{np.count_nonzero(changed)} 个像元")
    print(
        "耕地→建设用地（值 18）："
        f"{np.count_nonzero(result == 18)} 个像元"
    )
    print(f"输出文件：{output_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("start_year", type=int)
    parser.add_argument("end_year", type=int)
    args = parser.parse_args()

    if not (1990 <= args.start_year < args.end_year <= 2025):
        parser.error(
            "年份须满足 1990 ≤ 起始年份 < 结束年份 ≤ 2025"
        )

    create_raster(args.start_year, args.end_year)


if __name__ == "__main__":
    main()