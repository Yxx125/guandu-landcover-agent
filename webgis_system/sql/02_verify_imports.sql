-- 核对四张表的记录数与年份范围；只查询，不修改数据

SELECT 'land_class' AS table_name,
       COUNT(*) AS row_count,
       NULL::integer AS earliest_year,
       NULL::integer AS latest_year
FROM guandu.land_class

UNION ALL

SELECT 'annual_area',
       COUNT(*),
       MIN(year),
       MAX(year)
FROM guandu.annual_area

UNION ALL

SELECT 'annual_net_change',
       COUNT(*),
       MIN(start_year),
       MAX(end_year)
FROM guandu.annual_net_change

UNION ALL

SELECT 'transition_area',
       COUNT(*),
       MIN(start_year),
       MAX(end_year)
FROM guandu.transition_area;