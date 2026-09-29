SET client_encoding TO 'UTF8';
TRUNCATE TABLE guandu.transition_area, guandu.annual_net_change, guandu.annual_area;
\copy guandu.annual_area(year,class_code,class_name,pixel_count,area_km2,percentage) FROM '/import/annual_area_1990_2025_中文.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8');
\copy guandu.annual_net_change(start_year,end_year,class_code,class_name,start_area_km2,end_area_km2,net_change_km2) FROM '/import/annual_net_change_1991_2025.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8');
\copy guandu.transition_area(start_year,end_year,from_code,from_name,to_code,to_name,pixel_count,area_km2) FROM '/import/transition_1990_2025_中文.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8');

DO $$
DECLARE annual_count INTEGER; net_count INTEGER;
BEGIN
  SELECT COUNT(*) INTO annual_count FROM guandu.annual_area;
  SELECT COUNT(*) INTO net_count FROM guandu.annual_net_change;
  IF annual_count <> 324 THEN RAISE EXCEPTION 'annual_area expected 324 rows, got %', annual_count; END IF;
  IF net_count <> 315 THEN RAISE EXCEPTION 'annual_net_change expected 315 rows, got %', net_count; END IF;
END $$;
