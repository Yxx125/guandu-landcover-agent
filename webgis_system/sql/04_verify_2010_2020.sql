SELECT
    COUNT(*) AS matrix_rows,
    SUM(pixel_count) AS total_pixels,
    SUM(area_km2) AS total_area_km2
FROM guandu.transition_area
WHERE start_year = 2010
  AND end_year = 2020;
  WITH matrix_totals AS (
    SELECT 2010 AS year, from_code AS class_code,
           SUM(pixel_count) AS pixels
    FROM guandu.transition_area
    WHERE start_year = 2010 AND end_year = 2020
    GROUP BY from_code

    UNION ALL

    SELECT 2020 AS year, to_code AS class_code,
           SUM(pixel_count) AS pixels
    FROM guandu.transition_area
    WHERE start_year = 2010 AND end_year = 2020
    GROUP BY to_code
)
SELECT
    a.year,
    a.class_code,
    a.pixel_count AS annual_pixels,
    m.pixels AS matrix_pixels
FROM guandu.annual_area AS a
LEFT JOIN matrix_totals AS m
    ON m.year = a.year AND m.class_code = a.class_code
WHERE a.year IN (2010, 2020)
  AND a.pixel_count IS DISTINCT FROM m.pixels
ORDER BY a.year, a.class_code;