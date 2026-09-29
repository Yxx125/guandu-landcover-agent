"""检查 1990–2025 年官渡区 CLCD 栅格是否能逐像元比较。"""

from pathlib import Path

import numpy as np
import rasterio


RASTER_DIR = Path(r"F:\官渡区\3_output\clcd_guandu")
YEARS = range(1990, 2026)


def main():
    problems = []
    reference_grid = None
    reference_valid = None

    for year in YEARS:
        path = RASTER_DIR / f"guandu_{year}.tif"

        if not path.is_file():
            problems.append(f"{year}：文件不存在：{path}")
            continue

        with rasterio.open(path) as src:
            grid = (src.crs, src.width, src.height, src.transform)
            data = src.read(1, masked=True)
            values = data.data

            # 只有 1–9 才是 CLCD 地类；0 和 NoData 不参与统计。
            valid = (~np.ma.getmaskarray(data)) & (values >= 1) & (values <= 9)
            valid_count = int(np.count_nonzero(valid))

            if reference_grid is None:
                reference_grid = grid
                reference_valid = valid.copy()
                print(f"参考年份：{year}")
                print(f"坐标系：{src.crs}")
                print(f"宽 × 高：{src.width} × {src.height}")
                print(f"像元大小：{src.res}")
                print(f"有效像元：{valid_count}")
            else:
                if grid != reference_grid:
                    problems.append(f"{year}：坐标系、尺寸或网格原点不一致")
                if valid.shape != reference_valid.shape:
                    problems.append(f"{year}：有效区域形状与 1990 年不同")
                elif not np.array_equal(valid, reference_valid):
                    different = int(np.count_nonzero(valid != reference_valid))
                    problems.append(
                        f"{year}：有效区域与 1990 年相差 {different} 个像元"
                    )

            invalid = (~np.ma.getmaskarray(data)) & (
                (values < 0) | (values > 9)
            )
            if np.any(invalid):
                problems.append(f"{year}：存在超出 0–9 范围的像元值")

    if problems:
        print(f"\n检查发现 {len(problems)} 个问题：")
        for problem in problems:
            print("-", problem)
    else:
        print("\n检查通过：36 年栅格网格一致，有效区域一致。")


if __name__ == "__main__":
    main()