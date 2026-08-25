-- scripts/add_comments.sql：4 张 osm_* 表的中文 COMMENT（表 + 全部列，无缺漏）
-- 执行：docker exec -i aigis-postgis psql -U aigis -d aigis -v ON_ERROR_STOP=1 < scripts/add_comments.sql
-- 注：natural 是 SQL 保留字，列引用须加双引号。

-- ============ osm_pois 兴趣点 ============
COMMENT ON TABLE osm_pois IS '兴趣点（点）：餐饮/购物/景点等 amenity/shop/tourism 节点';
COMMENT ON COLUMN osm_pois.osm_id IS 'OSM 要素 ID（节点 ID，配合 osm_type 定位唯一要素）';
COMMENT ON COLUMN osm_pois.osm_type IS 'OSM 要素类型：N=节点 node, W=路径 way, R=关系 relation';
COMMENT ON COLUMN osm_pois.name IS '名称（取 OSM name 标签，北京数据多为中文名）';
COMMENT ON COLUMN osm_pois.amenity IS '设施类型：restaurant=餐厅, cafe=咖啡厅, fast_food=快餐, bank=银行, school=学校, hospital=医院, pharmacy=药房, parking=停车场, toilet=公厕 等';
COMMENT ON COLUMN osm_pois.shop IS '商店类型：supermarket=超市, convenience=便利店, bakery=面包店, clothes=服装店, restaurant 相关食品店 等';
COMMENT ON COLUMN osm_pois.tourism IS '旅游类型：attraction=景点, hotel=酒店, museum=博物馆, viewpoint=观景点, zoo=动物园 等';
COMMENT ON COLUMN osm_pois.cuisine IS '菜系（仅餐厅类）：chinese=中餐, regional=地方菜, noodle=面食, hotpot=火锅 等';
COMMENT ON COLUMN osm_pois.geom IS '几何（点，EPSG:4326 WGS84 经纬度）';

-- ============ osm_roads 道路 ============
COMMENT ON TABLE osm_roads IS '道路（线）：highway 类型，ref 含环路/高速编号如 S32/G2/G6';
COMMENT ON COLUMN osm_roads.osm_id IS 'OSM 要素 ID（路径 ID，配合 osm_type 定位唯一要素）';
COMMENT ON COLUMN osm_roads.osm_type IS 'OSM 要素类型：N=节点 node, W=路径 way, R=关系 relation';
COMMENT ON COLUMN osm_roads.name IS '道路名称（如"长安街""中关村大街"，取 OSM name 标签）';
COMMENT ON COLUMN osm_roads.highway IS '道路等级：motorway=高速, trunk=快速干道, primary=主干道, secondary=次干道, tertiary=支路, residential=居住区道路, living_street=生活性街道, service=辅助道路, pedestrian=步行街, footway=人行道, cycleway=自行车道 等';
COMMENT ON COLUMN osm_roads.ref IS '道路编号（如 S32=密云—涿州高速, G2=京沪高速, G6=京藏高速, Xxxx=县道）';
COMMENT ON COLUMN osm_roads.maxspeed IS '限速（km/h，整数；非数值标签如 none/signals/walk 及带单位值如 "50 mph" 置空）';
COMMENT ON COLUMN osm_roads.geom IS '几何（线，EPSG:4326 WGS84 经纬度）';

-- ============ osm_areas 面状地物 ============
COMMENT ON TABLE osm_areas IS '面状地物（公园/绿地/水体/建筑区等，含名称与分类标签）；绿化覆盖率口径：绿化 = leisure IN (park, garden) OR landuse IN (grass, forest, meadow) OR natural = wood';
COMMENT ON COLUMN osm_areas.osm_id IS 'OSM 要素 ID（way 或 relation 的 ID，配合 osm_type 定位唯一要素）';
COMMENT ON COLUMN osm_areas.osm_type IS 'OSM 要素类型：N=节点 node, W=路径 way, R=关系 relation';
COMMENT ON COLUMN osm_areas.name IS '名称（如"朝阳公园""奥林匹克森林公园"，取 OSM name 标签）';
COMMENT ON COLUMN osm_areas.leisure IS '休闲类型：park=公园, garden=花园, playground=儿童游乐场, pitch=运动场地, sports_centre=体育中心';
COMMENT ON COLUMN osm_areas.landuse IS '土地利用：grass=草地, forest=森林, meadow=牧场, farmland=耕地, residential=居住区, commercial=商业区, industrial=工业区, cemetery=墓地';
COMMENT ON COLUMN osm_areas."natural" IS '自然地物：wood=树林, water=水体, scrub=灌木丛, wetland=湿地, sand=沙地（绿化口径仅 wood 计入）';
COMMENT ON COLUMN osm_areas.building IS '建筑（yes=普通建筑, apartments=公寓, office=办公楼, retail=商业建筑；值为 no 时不入库）';
COMMENT ON COLUMN osm_areas.geom IS '几何（面/多面，EPSG:4326 WGS84 经纬度；way 为 polygon，relation 为 multipolygon）';

-- ============ osm_boundaries 行政区划 ============
COMMENT ON TABLE osm_boundaries IS '行政区划边界面（type=boundary 且 boundary=administrative 的 relation），admin_level: 4=北京市, 6=区县, 8/9=街道/乡镇';
COMMENT ON COLUMN osm_boundaries.osm_id IS 'OSM 要素 ID（relation ID，配合 osm_type 定位唯一要素）';
COMMENT ON COLUMN osm_boundaries.osm_type IS 'OSM 要素类型：N=节点 node, W=路径 way, R=关系 relation（本表基本为 R）';
COMMENT ON COLUMN osm_boundaries.name IS '区划名称（如"北京市""朝阳区""中关村街道"）';
COMMENT ON COLUMN osm_boundaries.admin_level IS '行政级别（整数）：4=省级（北京市）, 6=地市级（东城/西城/朝阳/海淀等区）, 8/9=街道/乡镇级';
COMMENT ON COLUMN osm_boundaries.boundary IS '边界类型（本表恒为 administrative=行政边界）';
COMMENT ON COLUMN osm_boundaries.geom IS '几何（多面，EPSG:4326 WGS84 经纬度；成员被数据裁剪截断的关系不入库）';

-- ============ 权限与统计 ============
GRANT SELECT ON ALL TABLES IN SCHEMA public TO aigis_readonly;
ANALYZE;
