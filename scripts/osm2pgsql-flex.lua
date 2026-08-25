-- scripts/osm2pgsql-flex.lua：北京 OSM → 4 张语义化表（osm2pgsql 2.3.1 flex 输出）
-- 用法：osm2pgsql --create --slim --drop --output=flex --style scripts/osm2pgsql-flex.lua \
--          --database aigis --host localhost --port 5432 --username aigis data/beijing-latest.osm.pbf
-- 表：osm_pois（点状 POI）/ osm_roads（道路线）/ osm_areas（面状地物）/ osm_boundaries（行政区划）

local tables = {}

tables.pois = osm2pgsql.define_table({
  name = 'osm_pois',
  ids = { type = 'any', id_column = 'osm_id', type_column = 'osm_type' },
  columns = {
    { column = 'name',    type = 'text' },
    { column = 'amenity', type = 'text' },
    { column = 'shop',    type = 'text' },
    { column = 'tourism', type = 'text' },
    { column = 'cuisine', type = 'text' },
    { column = 'geom', type = 'geometry', projection = 4326, not_null = true },
  },
})

tables.roads = osm2pgsql.define_table({
  name = 'osm_roads',
  ids = { type = 'any', id_column = 'osm_id', type_column = 'osm_type' },
  columns = {
    { column = 'name',     type = 'text' },
    { column = 'highway',  type = 'text' },
    { column = 'ref',      type = 'text' },
    { column = 'maxspeed', type = 'int' },
    { column = 'geom', type = 'geometry', projection = 4326, not_null = true },
  },
})

tables.areas = osm2pgsql.define_table({
  name = 'osm_areas',
  ids = { type = 'any', id_column = 'osm_id', type_column = 'osm_type' },
  columns = {
    { column = 'name',     type = 'text' },
    { column = 'leisure',  type = 'text' },
    { column = 'landuse',  type = 'text' },
    { column = 'natural',  type = 'text' },
    { column = 'building', type = 'text' },
    { column = 'geom', type = 'geometry', projection = 4326, not_null = true },
  },
})

tables.boundaries = osm2pgsql.define_table({
  name = 'osm_boundaries',
  ids = { type = 'any', id_column = 'osm_id', type_column = 'osm_type' },
  columns = {
    { column = 'name',        type = 'text' },
    { column = 'admin_level', type = 'int' },
    { column = 'boundary',    type = 'text' },
    { column = 'geom', type = 'geometry', projection = 4326, not_null = true },
  },
})

-- 注：osm2pgsql 2.3 回调必须挂在 osm2pgsql 表上（osm2pgsql.process_node），
--     旧版全局函数形式 function process_node() 已不再被调用（静默跳过）。
function osm2pgsql.process_node(o)
  if o.tags.amenity or o.tags.shop or o.tags.tourism then
    tables.pois:insert({
      name = o.tags.name, amenity = o.tags.amenity,
      shop = o.tags.shop, tourism = o.tags.tourism, cuisine = o.tags.cuisine,
      geom = o:as_point(),
    })
  end
end

-- 道路（线）+ 面状地物（闭合 way）+ 面状 POI 质心
-- 去重语义：同一 way 同时带面类标签（leisure/landuse/natural/building）和 POI 标签
-- （amenity/shop/tourism）时，areas 记面、pois 记质心，两者共存是正确行为
-- （北京大量门店标绘在建筑物轮廓上，way 级 POI 是召回率主要来源）。
function osm2pgsql.process_way(o)
  if o.tags.highway then
    tables.roads:insert({
      name = o.tags.name, highway = o.tags.highway,
      ref = o.tags.ref, maxspeed = tonumber(o.tags.maxspeed),
      geom = o:as_linestring(),
    })
  end
  if o.is_closed then
    if o.tags.leisure or o.tags.landuse or o.tags.natural
        or (o.tags.building and o.tags.building ~= 'no') then
      tables.areas:insert({
        name = o.tags.name, leisure = o.tags.leisure,
        landuse = o.tags.landuse, natural = o.tags.natural,
        building = o.tags.building, geom = o:as_polygon(),
      })
    end
    -- 面状 POI：闭合 way 上的门店/设施/景点，取面质心为点
    if o.tags.amenity or o.tags.shop or o.tags.tourism then
      local pt = o:as_polygon():centroid()
      if pt then
        tables.pois:insert({
          name = o.tags.name, amenity = o.tags.amenity,
          shop = o.tags.shop, tourism = o.tags.tourism, cuisine = o.tags.cuisine,
          geom = pt,
        })
      end
    end
  end
end

-- 行政区划（type=boundary 关系）+ 面状地物（type=multipolygon 关系）+ 面状 POI 质心
-- 注：城区提取数据可能裁剪掉部分成员 way，as_multipolygon() 会返回 nil，
--     geom 列为 not_null，故插入前必须判空跳过（否则整库导入报错回滚）。
function osm2pgsql.process_relation(o)
  if o.tags.boundary == 'administrative' and o.tags.admin_level then
    local geom = o:as_multipolygon()
    if geom then
      tables.boundaries:insert({
        name = o.tags.name,
        admin_level = tonumber(o.tags.admin_level), boundary = o.tags.boundary,
        geom = geom,
      })
    end
    return
  end
  local geom = o:as_multipolygon()
  if not geom then
    return
  end
  if o.tags.leisure or o.tags.landuse or o.tags.natural then
    tables.areas:insert({
      name = o.tags.name, leisure = o.tags.leisure,
      landuse = o.tags.landuse, natural = o.tags.natural,
      building = o.tags.building, geom = geom,
    })
  end
  -- 面状 POI：relation 型商场/校园/景点等，取多面质心为点（与 areas 共存）
  if o.tags.amenity or o.tags.shop or o.tags.tourism then
    local pt = geom:centroid()
    if pt then
      tables.pois:insert({
        name = o.tags.name, amenity = o.tags.amenity,
        shop = o.tags.shop, tourism = o.tags.tourism, cuisine = o.tags.cuisine,
        geom = pt,
      })
    end
  end
end
