# src/aigis/scenarios/geo.py
"""场景规划用纯几何工具（不依赖数据库，便于单测）。

坐标统一 (lng, lat) 元组；城市尺度（<100km）平面近似已足够：
经度差乘 cos(中位纬度) 折算成等效米制比例，k-means/贪心均在平面坐标上进行，
最终距离汇报用 haversine（米）。
"""
import math
import random

R_EARTH_M = 6371000.0
Point = tuple[float, float]  # (lng, lat)


def haversine_m(a: Point, b: Point) -> float:
    """两坐标点大圆距离（米）。"""
    lng1, lat1 = math.radians(a[0]), math.radians(a[1])
    lng2, lat2 = math.radians(b[0]), math.radians(b[1])
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2)
    return 2 * R_EARTH_M * math.asin(math.sqrt(h))


def _to_plane(points: list[Point]) -> list[tuple[float, float]]:
    """经纬度 → 平面近似坐标（度；经度按中位纬度压缩）。"""
    lat_mid = math.radians(sorted(p[1] for p in points)[len(points) // 2])
    return [(p[0] * math.cos(lat_mid), p[1]) for p in points]


def kmeans(points: list[Point], k: int, iters: int = 60,
           seeds: int = 8, rng: random.Random | None = None) -> list[int]:
    """Lloyd k-means，返回每个点的簇标号（0..k-1）。

    多次随机初始化取总平方误差最小解；迭代中出现空簇时把最大簇一分为二
    （质心 ± 微小偏移重播种），保证返回的标号集合恰为 min(k, n) 个连续簇。
    n <= 1 或 k < 1 时全部归 0 号簇。
    """
    n = len(points)
    if n == 0 or k < 1:
        return []
    k = min(k, n)
    if k == 1 or n == 1:
        return [0] * n
    rng = rng or random.Random(42)
    plane = _to_plane(points)
    best_labels, best_sse = None, math.inf
    for _ in range(seeds):
        centers = rng.sample(plane, k)
        labels = [0] * n
        for _ in range(iters):
            changed = False
            for i, p in enumerate(plane):
                j = min(range(k), key=lambda c: (plane_dist2(p, centers[c]), c))
                if labels[i] != j:
                    labels[i], changed = j, True
            if not changed:
                break
            for c in range(k):
                members = [plane[i] for i in range(n) if labels[i] == c]
                if members:  # 空簇：拆最大簇顶替（质心各偏一侧）
                    centers[c] = _mean(members)
                else:
                    biggest = max(range(k), key=lambda c2: sum(1 for l in labels if l == c2))
                    members = [plane[i] for i in range(n) if labels[i] == biggest]
                    if len(members) < 2:
                        continue
                    m = _mean(members)
                    centers[c] = (m[0] + 1e-5, m[1] + 1e-5)
                    centers[biggest] = (m[0] - 1e-5, m[1] - 1e-5)
        sse = sum(plane_dist2(plane[i], centers[labels[i]]) for i in range(n))
        if sse < best_sse:
            best_sse, best_labels = sse, labels[:]
    return _relabel_dense(best_labels)


def plane_dist2(a: tuple[float, float], b: tuple[float, float]) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def _mean(pts: list[tuple[float, float]]) -> tuple[float, float]:
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def _relabel_dense(labels: list[int]) -> list[int]:
    """簇标号压缩为 0..m-1 连续（按首次出现顺序）。"""
    mapping: dict[int, int] = {}
    return [mapping.setdefault(l, len(mapping)) for l in labels]


def greedy_order(points: list[Point], start: int = 0) -> list[int]:
    """最近邻贪心访问顺序：从 start 出发，每步去最近未访问点（返回索引序）。"""
    n = len(points)
    if n == 0:
        return []
    order = [start]
    remaining = set(range(n)) - {start}
    cur = start
    while remaining:
        nxt = min(remaining, key=lambda i: (haversine_m(points[cur], points[i]), i))
        order.append(nxt)
        remaining.remove(nxt)
        cur = nxt
    return order


def centroid(points: list[Point]) -> Point:
    """算术平均质心（城市尺度近似）。"""
    m = _mean([(p[0], p[1]) for p in points])
    return (m[0], m[1])
