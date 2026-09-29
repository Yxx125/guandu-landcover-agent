"""生成土地覆盖转移类型的 GeoServer SLD 样式。"""

import colorsys
from pathlib import Path
from xml.etree import ElementTree as ET


OUTPUT = (
    Path(r"F:\官渡区\webgis_system")
    / "styles"
    / "transition_types.sld"
)

NS_SLD = "http://www.opengis.net/sld"
NS_OGC = "http://www.opengis.net/ogc"

ET.register_namespace("", NS_SLD)
ET.register_namespace("ogc", NS_OGC)


def tag(name):
    return f"{{{NS_SLD}}}{name}"


def color_for(from_code, to_code):
    """按起始地类和目标地类生成稳定颜色。"""
    special = {
        (1, 8): "#E53935",  # 耕地→建设用地：红
        (7, 2): "#2E7D32",  # 裸地→林地：绿
        (4, 8): "#FB8C00",  # 草地→建设用地：橙
    }
    if (from_code, to_code) in special:
        return special[(from_code, to_code)]

    hue = (to_code - 1) / 9
    saturation = 0.55 + (from_code % 3) * 0.12
    brightness = 0.68 + (from_code // 3) * 0.09
    red, green, blue = colorsys.hsv_to_rgb(
        hue, saturation, min(brightness, 0.95)
    )
    return f"#{round(red * 255):02X}{round(green * 255):02X}{round(blue * 255):02X}"


def main():
    root = ET.Element(
        tag("StyledLayerDescriptor"),
        {"version": "1.0.0"},
    )
    named_layer = ET.SubElement(root, tag("NamedLayer"))
    ET.SubElement(named_layer, tag("Name")).text = "transition_types"

    user_style = ET.SubElement(named_layer, tag("UserStyle"))
    ET.SubElement(user_style, tag("Title")).text = (
        "Guandu land-cover transitions"
    )

    feature_style = ET.SubElement(user_style, tag("FeatureTypeStyle"))
    rule = ET.SubElement(feature_style, tag("Rule"))
    raster_symbolizer = ET.SubElement(rule, tag("RasterSymbolizer"))
    color_map = ET.SubElement(
        raster_symbolizer,
        tag("ColorMap"),
        {"type": "values"},
    )

    ET.SubElement(
        color_map,
        tag("ColorMapEntry"),
        {
            "color": "#FFFFFF",
            "quantity": "0",
            "label": "Outside or unchanged",
            "opacity": "0",
        },
    )

    for from_code in range(1, 10):
        for to_code in range(1, 10):
            if from_code == to_code:
                continue

            code = from_code * 10 + to_code
            ET.SubElement(
                color_map,
                tag("ColorMapEntry"),
                {
                    "color": color_for(from_code, to_code),
                    "quantity": str(code),
                    "label": f"{from_code} to {to_code}",
                    "opacity": "1",
                },
            )

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(
        OUTPUT,
        encoding="utf-8",
        xml_declaration=True,
    )
    print(f"已生成样式：{OUTPUT}")
    print("规则：0 透明；18 红色；72 绿色；48 橙色。")


if __name__ == "__main__":
    main()