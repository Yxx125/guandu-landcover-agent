SET client_encoding TO 'UTF8';
CREATE SCHEMA IF NOT EXISTS guandu;

CREATE TABLE IF NOT EXISTS guandu.land_class (
    class_code SMALLINT PRIMARY KEY CHECK (class_code BETWEEN 1 AND 9),
    class_name TEXT NOT NULL,
    color_hex CHAR(7) NOT NULL
);
INSERT INTO guandu.land_class (class_code, class_name, color_hex) VALUES
(1, '耕地', '#FAE39C'), (2, '林地', '#446F33'), (3, '灌丛', '#33A02C'),
(4, '草地', '#ABD37B'), (5, '水体', '#1E69B4'), (6, '积雪／冰川', '#A6CEE3'),
(7, '裸地', '#CFBDA3'), (8, '建设用地（不透水面）', '#E24290'), (9, '湿地', '#289BE8')
ON CONFLICT (class_code) DO UPDATE SET class_name=EXCLUDED.class_name, color_hex=EXCLUDED.color_hex;

CREATE TABLE IF NOT EXISTS guandu.annual_area (
    year SMALLINT NOT NULL CHECK (year BETWEEN 1990 AND 2025),
    class_code SMALLINT NOT NULL REFERENCES guandu.land_class(class_code),
    class_name TEXT NOT NULL, pixel_count INTEGER NOT NULL CHECK (pixel_count >= 0),
    area_km2 NUMERIC(14,4) NOT NULL CHECK (area_km2 >= 0),
    percentage NUMERIC(7,4) NOT NULL CHECK (percentage BETWEEN 0 AND 100),
    PRIMARY KEY (year, class_code)
);
CREATE TABLE IF NOT EXISTS guandu.annual_net_change (
    start_year SMALLINT NOT NULL, end_year SMALLINT NOT NULL,
    class_code SMALLINT NOT NULL REFERENCES guandu.land_class(class_code), class_name TEXT NOT NULL,
    start_area_km2 NUMERIC(14,4) NOT NULL, end_area_km2 NUMERIC(14,4) NOT NULL,
    net_change_km2 NUMERIC(14,4) NOT NULL, PRIMARY KEY (start_year,end_year,class_code),
    CHECK (start_year BETWEEN 1990 AND 2024), CHECK (end_year=start_year+1)
);
CREATE TABLE IF NOT EXISTS guandu.transition_area (
    start_year SMALLINT NOT NULL, end_year SMALLINT NOT NULL,
    from_code SMALLINT NOT NULL REFERENCES guandu.land_class(class_code), from_name TEXT NOT NULL,
    to_code SMALLINT NOT NULL REFERENCES guandu.land_class(class_code), to_name TEXT NOT NULL,
    pixel_count INTEGER NOT NULL CHECK (pixel_count >= 0), area_km2 NUMERIC(14,4) NOT NULL CHECK (area_km2 >= 0),
    PRIMARY KEY (start_year,end_year,from_code,to_code),
    CHECK (start_year >= 1990 AND end_year <= 2025), CHECK (start_year < end_year)
);
CREATE INDEX IF NOT EXISTS annual_area_year_idx ON guandu.annual_area(year);
CREATE INDEX IF NOT EXISTS net_change_years_idx ON guandu.annual_net_change(start_year,end_year);
CREATE INDEX IF NOT EXISTS transition_years_idx ON guandu.transition_area(start_year,end_year);
