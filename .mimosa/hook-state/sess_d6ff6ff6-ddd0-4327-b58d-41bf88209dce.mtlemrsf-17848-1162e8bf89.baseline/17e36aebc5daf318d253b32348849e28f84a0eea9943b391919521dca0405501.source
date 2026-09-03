"""schema_export 集成测试：需运行中的 PostGIS 容器（aigis-postgis）。"""
import pytest
from aigis.schema_export import export_schema

ADMIN = "host=localhost port=5432 dbname=aigis user=aigis password=aigis_dev_2026"


@pytest.mark.integration
def test_export_contains_comments_and_samples():
    text = export_schema(ADMIN)
    assert "osm_roads" in text and "COMMENT" in text
    assert "ring_areas" in text
    for t in ("osm_pois", "osm_areas", "osm_boundaries"):
        assert f"TABLE {t}" in text
