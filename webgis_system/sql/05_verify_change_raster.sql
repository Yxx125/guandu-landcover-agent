SELECT
    SUM(pixel_count) FILTER (
        WHERE from_code <> to_code
    ) AS changed_pixels,
    SUM(pixel_count) FILTER (
        WHERE from_code = 1 AND to_code = 8
    ) AS farmland_to_built_pixels
FROM guandu.transition_area
WHERE start_year = 2010
  AND end_year = 2020;