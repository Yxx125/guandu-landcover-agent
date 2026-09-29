"""从 GeoServer 实际渲染的九类栅格图例中提取色块颜色。"""

from io import BytesIO

from PIL import Image, UnidentifiedImageError


def extract_legend_colors(png_bytes: bytes) -> list[str]:
    """读取九类有标签的离散色块；忽略第 0 类 NoData 的叉形图例。

    GeoServer GetLegendGraphic 的每个色块在左侧形成至少 12 像素高的
    纯色竖段。NoData 是叉号，不会形成这样的连续纯色段。
    如果样式不是预期布局，显式报错，避免展示错误颜色。
    """
    try:
        image = Image.open(BytesIO(png_bytes)).convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError("GeoServer 未返回有效的 PNG 图例") from exc
    if image.width < 15 or image.height < 120:
        raise ValueError("GeoServer 图例尺寸异常")
    x = 10
    segments = []
    start = 0
    previous = image.getpixel((x, 0))
    for y in range(1, image.height + 1):
        color = image.getpixel((x, y)) if y < image.height else None
        if color == previous:
            continue
        length = y - start
        if (length >= 12 and previous != (255, 255, 255)
                and not all(channel >= 250 for channel in previous)):
            segments.append((start, previous))
        previous, start = color, y
    if len(segments) != 9:
        raise ValueError(f"GeoServer 图例应有九个地类色块，实际识别到 {len(segments)} 个")
    return ["#%02X%02X%02X" % color for _, color in segments]
