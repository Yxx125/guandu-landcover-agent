"""脚本名称：build_knowledge_graph.py

从官渡区年度面积表和相邻年份的原始对齐栅格构建 Neo4j 土地覆盖知识图谱。
不保存数据库密码；运行时安全输入。重复运行使用 MERGE 更新已有节点和关系。
"""

import getpass
import os
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from neo4j import GraphDatabase

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))
from app import FIRST_YEAR, LAST_YEAR, _transition_rows_from_rasters  # noqa: E402
from config import database_connect_kwargs  # noqa: E402


def annual_records():
    sql = """
        SELECT year, class_code, class_name, area_km2, pixel_count
        FROM guandu.annual_area
        WHERE year BETWEEN %s AND %s
        ORDER BY year, class_code
    """
    kwargs = database_connect_kwargs()
    if not kwargs.get("password") and not os.environ.get("CI"):
        kwargs["password"] = getpass.getpass("PostgreSQL 数据库密码（输入不显示）：")
    with psycopg.connect(**kwargs, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, (FIRST_YEAR, LAST_YEAR))
            rows = cursor.fetchall()
    if len(rows) != (LAST_YEAR - FIRST_YEAR + 1) * 9:
        raise RuntimeError(f"年度面积表应有 324 行，实际得到 {len(rows)} 行")
    return [
        {
            "year": int(row["year"]),
            "code": int(row["class_code"]),
            "name": row["class_name"],
            "area_km2": float(row["area_km2"]),
            "pixel_count": int(row["pixel_count"]),
            "id": f'{row["year"]}_{row["class_code"]}',
        }
        for row in rows
    ]


def main():
    neo4j_user = os.environ.get("NEO4J_USER", "neo4j")
    neo4j_password = os.environ.get("NEO4J_PASSWORD")
    if not neo4j_password:
        neo4j_password = getpass.getpass("Neo4j 密码（输入不显示）：")
    neo4j_uri = os.environ.get("NEO4J_URI", "bolt://127.0.0.1:7687")
    database = os.environ.get("NEO4J_DATABASE", "neo4j")
    annual = annual_records()
    with GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password)) as driver:
        driver.verify_connectivity()
        for statement in [
            "CREATE CONSTRAINT region_name IF NOT EXISTS FOR (n:Region) REQUIRE n.name IS UNIQUE",
            "CREATE CONSTRAINT year_value IF NOT EXISTS FOR (n:Year) REQUIRE n.value IS UNIQUE",
            "CREATE CONSTRAINT land_class_code IF NOT EXISTS FOR (n:LandClass) REQUIRE n.code IS UNIQUE",
            "CREATE CONSTRAINT observation_id IF NOT EXISTS FOR (n:Observation) REQUIRE n.id IS UNIQUE",
        ]:
            driver.execute_query(statement, database_=database)
        driver.execute_query(
            """
            MERGE (r:Region {name: '官渡区'})
            WITH r
            UNWIND $rows AS row
            MERGE (y:Year {value: row.year})
            MERGE (c:LandClass {code: row.code})
            SET c.name = row.name
            MERGE (o:Observation {id: row.id})
            SET o.year = row.year, o.class_code = row.code,
                o.area_km2 = row.area_km2, o.pixel_count = row.pixel_count
            MERGE (r)-[:HAS_OBSERVATION]->(o)
            MERGE (o)-[:IN_YEAR]->(y)
            MERGE (o)-[:OF_CLASS]->(c)
            """,
            rows=annual, database_=database,
        )
        print(f"[完成] 年度观测节点：{len(annual)}")
        total_relationships = 0
        for start_year in range(FIRST_YEAR, LAST_YEAR):
            end_year = start_year + 1
            matrix = _transition_rows_from_rasters(start_year, end_year)
            transitions = [
                {
                    "source": f"{start_year}_{item['from_code']}",
                    "target": f"{end_year}_{item['to_code']}",
                    "pixel_count": item["pixel_count"],
                    "area_km2": item["area_km2"],
                }
                for item in matrix
                if item["pixel_count"] > 0 and item["from_code"] != item["to_code"]
            ]
            driver.execute_query(
                """
                UNWIND $rows AS row
                MATCH (a:Observation {id: row.source})
                MATCH (b:Observation {id: row.target})
                MERGE (a)-[t:TRANSITIONS_TO]->(b)
                SET t.pixel_count = row.pixel_count, t.area_km2 = row.area_km2
                """,
                rows=transitions, database_=database,
            )
            total_relationships += len(transitions)
            print(f"[完成] {start_year}→{end_year}：{len(transitions)} 条转换关系")
        print(f"知识图谱导入完成：324 个年度观测节点，{total_relationships} 条变化关系。")


if __name__ == "__main__":
    main()
