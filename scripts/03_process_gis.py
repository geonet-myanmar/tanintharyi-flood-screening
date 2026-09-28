"""
Join the supplied screening products to MIMU geography and run the spatial
analysis the dashboard reports.

Inputs:  data/raw/*.geojson        (MIMU, from 01_download_mimu.py)
         data/processed/source.json, basins.geojson, streams.geojson (from 02)

The dashboard draws these vectors itself -- MIMU is the basemap. Admin polygons
are simplified to ~40 m and coordinates rounded to 4-5 decimals, well inside
the 1:250,000 source accuracy. Distances and areas are measured in UTM 47N
(EPSG:32647), the CRS the supplied screening used.

Analysis written to data/processed/analysis.json:
  townships   per township: MIMU villages vs screened points, distance
              classes, priorities, reported villages, schools, health
              facilities, land below 5 m, road-stream crossings
  reported    the 38 Dawei Watch villages matched to MIMU Pcodes and to the
              screening, plus their own stream distance
  crosstab    how the reported villages fall across the screening classes
  facilities  schools / hospitals within 200 m of a mapped stream, and within
              1 km of a reported or Immediate-Field-Check village
  basins      per HydroBASINS polygon: villages, near-stream, reported
"""

import json
import os
import warnings

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point, box
from shapely.ops import unary_union

warnings.filterwarnings("ignore", message="Geometry is in a geographic CRS")

HERE = os.path.dirname(__file__)
RAW = os.path.join(HERE, "..", "data", "raw")
OUT = os.path.join(HERE, "..", "data", "processed")

UTM = 32647
AOI_TS = ["MMR006001", "MMR006002", "MMR006003", "MMR006007", "MMR006008"]
TS_ORDER = ["Dawei", "Launglon", "Thayetchaung", "Palaw", "Tanintharyi"]
VIEW = box(97.55, 11.15, 99.85, 15.2)
TOL_ADM = 0.0004
TOL_CTX = 0.002
NEAR_STREAM_M = 200
NEAR_SITE_M = 1000
PRIORITIES = ["Immediate Field Check", "Enhanced Monitoring", "Monitoring"]
CLASSES = ["0-200 m", "200-500 m", ">500 m", "Not screened - outside basin coverage"]


def load(name, folder=RAW):
    return gpd.read_file(os.path.join(folder, name))


def rnd(obj, p):
    if isinstance(obj, (list, tuple)):
        if obj and isinstance(obj[0], (int, float)):
            return [round(float(v), p) for v in obj]
        return [rnd(v, p) for v in obj]
    return obj


def dump(gdf, name, keep, prec=4):
    feats = []
    for _, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        g = geom.__geo_interface__
        props = {}
        for k in keep:
            v = row.get(k)
            if v is None or (isinstance(v, float) and pd.isna(v)) or v == "":
                continue
            if hasattr(v, "item"):
                v = v.item()
            if isinstance(v, float):
                v = round(v, 3)
            props[k] = v
        feats.append({"type": "Feature", "properties": props,
                      "geometry": {"type": g["type"], "coordinates": rnd(g["coordinates"], prec)}})
    path = os.path.join(OUT, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": feats}, f,
                  ensure_ascii=False, separators=(",", ":"))
    print(f"  -> {name:26s} {len(feats):5,d} features  {os.path.getsize(path)/1024:8.1f} KB")


def km2(geom):
    return float(gpd.GeoSeries([geom], crs=4326).to_crs(UTM).area.sum() / 1e6)


def main():
    src = json.load(open(os.path.join(OUT, "source.json"), encoding="utf-8"))
    streams = load("streams.geojson", OUT)
    basins = load("basins.geojson", OUT)
    streams_u = streams.to_crs(UTM)
    stream_union_u = unary_union(streams_u.geometry)

    # ------------------------------------------------------------ admin
    print("admin")
    ts_all = load("adm3_townships.geojson")
    ts = ts_all[ts_all.TS_PCODE.isin(AOI_TS)].copy()
    aoi = unary_union(ts.geometry)
    aoi_u = gpd.GeoSeries([aoi], crs=4326).to_crs(UTM).iloc[0]
    near = aoi.buffer(0.03)

    a1 = load("adm1_states.geojson")
    myanmar = unary_union(a1.geometry)
    ctx = ts_all[~ts_all.TS_PCODE.isin(AOI_TS) & ts_all.intersects(VIEW)].copy()
    ctx["geometry"] = ctx.geometry.intersection(VIEW.buffer(0.2)).simplify(TOL_CTX, preserve_topology=True)
    dump(ctx, "context_townships.geojson", ["TS", "TS_MMR", "DT", "ST"])

    rest = VIEW.buffer(0.2).difference(myanmar)
    parts = list(rest.geoms) if hasattr(rest, "geoms") else [rest]
    east = VIEW.buffer(0.2).bounds[2]
    thai = [p for p in parts if p.bounds[2] >= east - 1e-6 and p.area > 0.01]
    dump(gpd.GeoDataFrame({"name": ["Thailand"]}, geometry=[unary_union(thai).simplify(TOL_CTX)], crs=4326),
         "thailand.geojson", ["name"])

    dt = load("adm2_districts.geojson")
    dt = dt[dt.DT_PCODE.isin(["MMR006D001", "MMR006D002"])].copy()
    dt["geometry"] = dt.geometry.intersection(VIEW.buffer(0.2)).simplify(TOL_ADM)
    dump(dt, "districts.geojson", ["DT", "DT_MMR", "DT_PCODE"])

    # ------------------------------------------------------------ villages
    print("villages")
    vp = load("village_points_tni.geojson")
    vp = vp[vp.TS_PCODE.isin(AOI_TS)].copy()
    vp["pcode"] = vp.VLG_PCODE.astype(int).astype(str)
    scr = {v["pcode"]: v for v in src["villages"]}
    missing_from_screen = vp[~vp.pcode.isin(scr)]
    print(f"  MIMU villages in AOI {len(vp)}, screened {len(scr)}, "
          f"MIMU not in screen {len(missing_from_screen)}, screen not in MIMU {len(set(scr) - set(vp.pcode))}")

    # reported villages -> MIMU pcode (by Myanmar name within township, nearest to the KML point)
    rep = src["reported"]
    for r in rep:
        cand = vp[(vp.TS == r["ts"]) & (vp.VLG_MMR.str.strip() == r["mm"])]
        pt = Point(r["lon"], r["lat"])
        if len(cand):
            d = gpd.GeoSeries(cand.geometry, crs=4326).to_crs(UTM).distance(
                gpd.GeoSeries([pt], crs=4326).to_crs(UTM).iloc[0])
            best = cand.loc[d.idxmin()]
            r.update(pcode=best.pcode, en=best.VILLAGE,
                     match="MIMU name", match_m=round(float(d.min()), 1))
        else:
            r.update(pcode=None, en=None, match="not in MIMU village list")
    # own stream distance for every reported village (not all are necessarily screened)
    rp_u = gpd.GeoSeries([Point(r["lon"], r["lat"]) for r in rep], crs=4326).to_crs(UTM)
    for r, g in zip(rep, rp_u):
        r["stream_m"] = round(float(g.distance(stream_union_u)), 1)
        s = scr.get(r["pcode"] or "")
        if s:
            r.update(cls=s["cls"], dist=s["dist"], basin=s["basin"], priority=s.get("priority"),
                     strahler=s.get("strahler"), elev=s.get("elev"))
        else:
            r.update(cls="Not in screening")
    unmatched = [r["mm"] for r in rep if not r["pcode"]]
    print(f"  reported {len(rep)}: matched to MIMU {len(rep) - len(unmatched)}; unmatched {unmatched}; "
          f"max offset {max(r.get('match_m', 0) for r in rep)} m")
    rep_pc = {r["pcode"]: r for r in rep if r["pcode"]}

    vp["rep"] = vp.pcode.map(lambda p: 1 if p in rep_pc else 0)
    for col in ("dist", "cls", "basin", "priority", "elev", "strahler", "gsm", "edge", "hybas", "dem_conf"):
        vp[col] = vp.pcode.map(lambda p, c=col: scr.get(p, {}).get(c))
    vp["cls"] = vp.cls.fillna("Not in supplied screening")
    vp["hz"] = vp.pcode.map(lambda p: rep_pc[p]["hazard"] if p in rep_pc else None)

    # land below 5 m (MIMU)
    lo = load("below5m.geojson")
    lo_d = unary_union(lo.geometry.buffer(0)).intersection(aoi)
    vp["low5"] = vp.geometry.within(lo_d).astype(int)
    for r in rep:
        r["low5"] = int(Point(r["lon"], r["lat"]).within(lo_d))

    vt_all = load("adm4_vt_tanintharyi.geojson")
    vt = vt_all[vt_all.TS_PCODE.isin(AOI_TS)].copy()
    wards_raw = load("adm5_wards.geojson")
    for r in rep:
        pt = Point(r["lon"], r["lat"])
        hit = vt[vt.contains(pt)]
        if len(hit):
            r["vt"], r["vt_mm"], r["vt_pcode"] = hit.iloc[0].VT, hit.iloc[0].VT_MMR, hit.iloc[0].VT_PCODE
        w = wards_raw[wards_raw.contains(pt)]
        r["ward"] = f"{w.iloc[0].WARD}, {w.iloc[0].TOWN}" if len(w) else None
    dump(vp, "villages.geojson", ["VILLAGE", "VLG_MMR", "pcode", "TS", "VT", "VT_PCODE", "dist", "cls", "basin",
                                  "priority", "elev", "strahler", "gsm", "edge", "rep", "hz", "low5", "dem_conf"],
         prec=5)

    # ------------------------------------------------------------ facilities
    print("facilities")
    hs = load("health_facilities.geojson")
    hs = hs[hs.TS_PCODE.isin(AOI_TS)].copy()
    sc = load("schools_lower.geojson")
    sc = sc[sc.mm_tscode.isin(AOI_TS)].copy()
    sc["geometry"] = sc.geometry.apply(lambda g: g.geoms[0] if g.geom_type == "MultiPoint" else g)
    sc = sc.set_geometry("geometry")

    rep_u = gpd.GeoDataFrame(rep, geometry=[Point(r["lon"], r["lat"]) for r in rep], crs=4326).to_crs(UTM)
    imm = vp[vp.priority == "Immediate Field Check"].to_crs(UTM)

    def exposure(gdf, name_col):
        u = gdf.to_crs(UTM)
        gdf["stream_m"] = u.geometry.distance(stream_union_u).round(0).values
        gdf["near_rep"] = [int(rep_u.distance(g).min() <= NEAR_SITE_M) for g in u.geometry]
        gdf["near_imm"] = [int(imm.distance(g).min() <= NEAR_SITE_M) for g in u.geometry]
        gdf["rep_m"] = [round(float(rep_u.distance(g).min())) for g in u.geometry]
        gdf["rep_near"] = [rep_u.loc[rep_u.distance(g).idxmin(), "mm"] for g in u.geometry]
        gdf["low5"] = gdf.geometry.within(lo_d).astype(int)
        return gdf

    hs = exposure(hs, "nmHsp_eng")
    sc = exposure(sc, "schoolname")
    hs["TS"] = hs.TS_en
    sc["TS"] = sc.mm_tsname
    dump(hs, "hospitals.geojson", ["nmHsp_eng", "nmHsp_mya", "lvlHsp_eng", "bedClass", "TS", "stream_m",
                                   "near_rep", "near_imm", "rep_m", "rep_near", "low5"], prec=5)
    dump(sc, "schools.geojson", ["schoolname", "urbanrural", "TS", "stream_m", "near_rep", "near_imm",
                                 "rep_m", "rep_near", "low5"], prec=5)

    # ------------------------------------------------------------ roads & crossings
    print("infrastructure")
    rd = load("roads.geojson")
    rd = rd[rd.intersects(near)].copy()
    rd["geometry"] = rd.geometry.intersection(VIEW)
    rd = rd[~rd.geometry.is_empty]
    # road x stream crossings (Strahler >= 2 -- order-1 headwater lines are too
    # generalised to place a crossing): candidate culverts / bridges / overtopping points
    st2 = streams[streams.ord >= 2]
    xs = []
    for _, r in rd.iterrows():
        cand = st2[st2.intersects(r.geometry)]
        for _, s in cand.iterrows():
            inter = r.geometry.intersection(s.geometry)
            pts = [inter] if inter.geom_type == "Point" else (
                list(inter.geoms) if hasattr(inter, "geoms") else [])
            for p in pts:
                if p.geom_type == "Point" and p.within(aoi):
                    xs.append(dict(road=r.Road_Type, ord=int(s.ord), q=float(s.q), geometry=p))
    xg = gpd.GeoDataFrame(xs, geometry="geometry", crs=4326)
    xg["TS"] = gpd.sjoin(xg, ts[["TS", "geometry"]], predicate="within", how="left").TS.values
    # nearest MIMU bridge within 300 m = mapped structure
    br = load("bridges.geojson")
    br = br[br.within(near)].copy()
    bru = br.to_crs(UTM)
    xgu = xg.to_crs(UTM)
    xg["bridge_m"] = [round(float(bru.distance(g).min())) if len(bru) else None for g in xgu.geometry]
    xg["bridged"] = (xg.bridge_m <= 300).astype(int)
    dump(xg, "crossings.geojson", ["road", "ord", "q", "TS", "bridged"], prec=5)
    rd["geometry"] = rd.geometry.simplify(0.0003, preserve_topology=True)
    dump(rd, "roads.geojson", ["Road_Type", "Route"])
    dump(br, "bridges.geojson", ["nmEng", "nmMya", "length_ft", "nmTsEng"], prec=5)

    rl = load("railways.geojson")
    rl["geometry"] = rl.geometry.intersection(VIEW).simplify(0.0003)
    dump(rl[~rl.geometry.is_empty], "railway.geojson", ["Div_Name"])

    towns = load("town_points.geojson")
    towns = towns[towns.intersects(VIEW)]
    dump(towns, "towns.geojson", ["Town", "Town_MMR4", "Township", "Level"], prec=5)
    wards = load("adm5_wards.geojson")
    wards = wards[wards.TS_PCODE.isin(AOI_TS)].copy()
    wards["geometry"] = wards.geometry.simplify(0.0001, preserve_topology=True)
    dump(wards, "wards.geojson", ["WARD", "WARD_MMR", "TOWN", "TS"], prec=5)

    sez = load("sez_polygons.geojson")
    dump(sez[sez.intersects(near)], "sez.geojson", ["nmEng", "nmMya", "zoneType"])
    ap = load("airports.geojson")
    dump(ap[ap.within(near)], "airports.geojson", ["Name_1", "TS", "Status"], prec=5)

    lo_g = gpd.GeoDataFrame(geometry=[lo_d.simplify(0.0004)], crs=4326)
    dump(lo_g, "lowland.geojson", [])

    rv = load("rivers_detail.geojson")
    rv = rv[rv.intersects(VIEW)].copy()
    rv["name"] = rv.NAME.replace({"TAVOY": "Dawei River", "TENASSERIM": "Tanintharyi River", "UNK": ""})
    rv["geometry"] = rv.geometry.intersection(VIEW).simplify(0.0003, preserve_topology=True)
    dump(rv, "rivers.geojson", ["name"])

    # ------------------------------------------------------------ village tracts
    vt["villages"] = vt.VT_PCODE.map(vp.groupby("VT_PCODE").size()).fillna(0).astype(int)
    vt["near"] = vt.VT_PCODE.map(vp[vp.cls == "0-200 m"].groupby("VT_PCODE").size()).fillna(0).astype(int)
    vt["imm"] = vt.VT_PCODE.map(vp[vp.priority == PRIORITIES[0]].groupby("VT_PCODE").size()).fillna(0).astype(int)
    vt["rep"] = vt.VT_PCODE.map(vp[vp.rep == 1].groupby("VT_PCODE").size()).fillna(0).astype(int)
    vt["geometry"] = vt.geometry.simplify(TOL_ADM, preserve_topology=True)
    dump(vt, "village_tracts.geojson", ["VT", "VT_MMR", "VT_PCODE", "TS", "villages", "near", "imm", "rep"])

    # ------------------------------------------------------------ basins
    bst = []
    for _, b in basins.iterrows():
        inb = vp[vp.hybas == b.hybas]
        bst.append(dict(hybas=b.hybas, group=b.group, sub_km2=b.sub_km2, up_km2=b.up_km2,
                        villages=len(inb), near=int((inb.cls == "0-200 m").sum()),
                        imm=int((inb.priority == PRIORITIES[0]).sum()), rep=int(inb.rep.sum())))
    basins["villages"] = [x["villages"] for x in bst]
    basins["near"] = [x["near"] for x in bst]
    basins["rep"] = [x["rep"] for x in bst]
    dump(basins, "basins.geojson", ["hybas", "group", "sub_km2", "up_km2", "coast", "villages", "near", "rep"])

    # ------------------------------------------------------------ township analysis
    print("analysis")
    tsu = ts.to_crs(UTM)
    ts["area_km2"] = (tsu.area / 1e6).round(0)
    rp = ts.geometry.representative_point()
    ts["lx"], ts["ly"] = rp.x.round(4), rp.y.round(4)
    by_ts = {}
    for _, t in ts.iterrows():
        v = vp[vp.TS_PCODE == t.TS_PCODE]
        rr = [r for r in rep if r["ts"] == t.TS]
        x = xg[xg.TS == t.TS]
        by_ts[t.TS] = dict(
            ts=t.TS, ts_mm=t.TS_MMR, pcode=t.TS_PCODE, dt=t.DT, area_km2=int(t.area_km2),
            mimu_villages=len(v), screened=int(v.pcode.isin(scr).sum()),
            village_tracts=int((vt.TS_PCODE == t.TS_PCODE).sum()),
            cls={c: int((v.cls == c).sum()) for c in CLASSES},
            priority={p: int((v.priority == p).sum()) for p in PRIORITIES},
            gsm=int((v.gsm == 1).sum()),
            reported=len(rr),
            reported_hz={h: sum(1 for r in rr if r["hazard"] == h) for h in sorted({r["hazard"] for r in rep})},
            reported_near=sum(1 for r in rr if r.get("cls") == "0-200 m"),
            schools=int((sc.mm_tscode == t.TS_PCODE).sum()),
            schools_near_stream=int(((sc.mm_tscode == t.TS_PCODE) & (sc.stream_m <= NEAR_STREAM_M)).sum()),
            schools_near_rep=int(((sc.mm_tscode == t.TS_PCODE) & (sc.near_rep == 1)).sum()),
            hospitals=int((hs.TS_PCODE == t.TS_PCODE).sum()),
            lowland_km2=round(km2(lo_d.intersection(t.geometry)), 1),
            lowland_pct=round(100 * km2(lo_d.intersection(t.geometry)) / km2(t.geometry), 1),
            villages_low5=int(v.low5.sum()),
            crossings=len(x), crossings_unbridged=int((x.bridged == 0).sum()),
        )
    ts["reported"] = ts.TS.map(lambda n: by_ts[n]["reported"])
    ts["near"] = ts.TS.map(lambda n: by_ts[n]["cls"]["0-200 m"])
    ts["geometry"] = ts.geometry.simplify(TOL_ADM, preserve_topology=True)
    dump(ts, "townships.geojson", ["TS", "TS_MMR", "TS_PCODE", "DT", "area_km2", "lx", "ly", "reported", "near"])

    # reported villages vs screening classes
    cross = {}
    for r in rep:
        k = r.get("priority") or r["cls"]
        cross[k] = cross.get(k, 0) + 1
    screened_rep = [r for r in rep if r["cls"] in CLASSES]
    rate = {}
    for c in CLASSES[:3]:
        n_all = int((vp.cls == c).sum())
        n_rep = sum(1 for r in screened_rep if r["cls"] == c)
        rate[c] = dict(villages=n_all, reported=n_rep, pct=round(100 * n_rep / n_all, 1) if n_all else 0)

    # facilities list for the table
    fac = []
    for kind, layer, nm in (("health", hs, "nmHsp_eng"), ("school", sc, "schoolname")):
        for _, f in layer.iterrows():
            if f.stream_m <= NEAR_STREAM_M or f.near_rep or f.near_imm:
                fac.append(dict(kind=kind, name=f[nm], ts=f.TS, stream_m=int(f.stream_m),
                                rep_m=int(f.rep_m), rep_near=f.rep_near, near_rep=int(f.near_rep),
                                near_imm=int(f.near_imm), low5=int(f.low5),
                                lon=round(f.geometry.x, 5), lat=round(f.geometry.y, 5)))
    fac.sort(key=lambda r: (r["kind"] != "health", -r["near_rep"], r["rep_m"]))

    dist_all = [v["dist"] for v in src["villages"] if v["dist"] is not None]
    tot = dict(
        mimu_villages=len(vp), screened=len(scr), not_screened_mimu=missing_from_screen[["VILLAGE", "VLG_MMR", "TS", "pcode"]].to_dict("records"),
        village_tracts=len(vt), area_km2=int(round(km2(aoi))),
        cls={c: int((vp.cls == c).sum()) for c in CLASSES},
        priority={p: int((vp.priority == p).sum()) for p in PRIORITIES},
        gsm=int((vp.gsm == 1).sum()), reported=len(rep),
        reported_hz={h: sum(1 for r in rep if r["hazard"] == h) for h in sorted({r["hazard"] for r in rep})},
        schools=len(sc), hospitals=len(hs),
        schools_near_stream=int((sc.stream_m <= NEAR_STREAM_M).sum()),
        hospitals_near_stream=int((hs.stream_m <= NEAR_STREAM_M).sum()),
        schools_near_rep=int(sc.near_rep.sum()), hospitals_near_rep=int(hs.near_rep.sum()),
        schools_near_imm=int(sc.near_imm.sum()), hospitals_near_imm=int(hs.near_imm.sum()),
        crossings=len(xg), crossings_unbridged=int((xg.bridged == 0).sum()), bridges=len(br),
        lowland_km2=round(km2(lo_d), 0), villages_low5=int(vp.low5.sum()),
        reported_low5=sum(r["low5"] for r in rep),
        streams=len(streams), stream_km=round(float(streams_u.length.sum() / 1000)),
        median_dist=round(float(pd.Series(dist_all).median())),
        reported_stream_median=round(float(pd.Series([r["stream_m"] for r in rep]).median())),
    )
    analysis = dict(townships={k: by_ts[k] for k in TS_ORDER}, total=tot, reported=rep, crosstab=cross,
                    rate=rate, facilities=fac, basins=bst, gsm=src["gsm_priority"],
                    audit=src["audit"], kyainseikgyi_bbox=src["kyainseikgyi_bbox"],
                    near_stream_m=NEAR_STREAM_M, near_site_m=NEAR_SITE_M,
                    dist_hist=[v["dist"] for v in src["villages"] if v["dist"] is not None])
    with open(os.path.join(OUT, "analysis.json"), "w", encoding="utf-8") as f:
        json.dump(analysis, f, ensure_ascii=False, indent=1, default=str)

    print("\nTotals:", {k: v for k, v in tot.items() if k != "not_screened_mimu"})
    print("MIMU not screened:", tot["not_screened_mimu"])
    print("Reported x screening:", cross)
    print("Report rate by class:", rate)
    for t in by_ts.values():
        print("  ", t["ts"], {k: t[k] for k in ("mimu_villages", "screened", "reported", "reported_near",
                                                "schools", "schools_near_stream", "hospitals", "lowland_pct",
                                                "villages_low5", "crossings", "crossings_unbridged")})


if __name__ == "__main__":
    main()
