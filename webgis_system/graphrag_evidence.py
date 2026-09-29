"""为解释类问答获取可追溯的图谱路径；只读，最多两跳。"""

import os


def graph_paths(start_year: int, end_year: int, class_codes: list[int]):
    """返回图中一至两跳关系及状态；两跳不代表同一像元的真实轨迹。"""
    password = os.environ.get("NEO4J_PASSWORD")
    if not password:
        return [], "Neo4j 未配置密码"
    if not class_codes or start_year >= end_year:
        return [], "该时间范围没有逐年转换路径"
    try:
        from neo4j import GraphDatabase

        uri = os.environ.get("NEO4J_URI", "bolt://127.0.0.1:7687")
        with GraphDatabase.driver(uri, auth=(os.environ.get("NEO4J_USER", "neo4j"), password)) as driver:
            records, _, _ = driver.execute_query(
                """
                MATCH p=(a:Observation)-[:TRANSITIONS_TO*1..2]->(b:Observation)
                WHERE a.year >= $start_year AND b.year <= $end_year
                  AND (a.class_code IN $class_codes OR b.class_code IN $class_codes)
                WITH nodes(p) AS obs, relationships(p) AS steps
                ORDER BY reduce(v=0.0, t IN steps | v + t.area_km2) DESC
                LIMIT 8
                RETURN [n IN obs | {year:n.year, class_code:n.class_code}] AS observations,
                       [r IN steps | {area_km2:r.area_km2, pixel_count:r.pixel_count}] AS transitions
                """,
                start_year=start_year, end_year=end_year, class_codes=class_codes,
                database_=os.environ.get("NEO4J_DATABASE", "neo4j"),
            )
        paths = [dict(record) for record in records]
        return paths, "ok；两跳仅为类别关系链，不代表同一像元轨迹" if paths else "图谱中无匹配路径"
    except Exception as exc:
        # 模型不接收数据库异常及连接细节；调用端会显示证据未就绪。
        print(f"图谱路径检索失败：{type(exc).__name__}: {exc}")
        return [], "图谱路径检索未就绪"
