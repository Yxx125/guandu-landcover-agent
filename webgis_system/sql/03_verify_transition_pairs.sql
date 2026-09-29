-- 查看已入库的地类转移矩阵对应哪些年份区间
SELECT
    start_year,
    end_year,
    COUNT(*) AS transition_rows,
    SUM(pixel_count) AS total_pixels,
    SUM(area_km2) AS total_area_km2
FROM guandu.transition_area
GROUP BY start_year, end_year
ORDER BY start_year, end_year;