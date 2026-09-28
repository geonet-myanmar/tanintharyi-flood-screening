"""
Unpack the supplied archive and parse every layer the dashboard uses.

Input: data/source/tanintharyi_flood_9_2026.rar -- despite the extension it is a
ZIP (PK header) whose member names are UTF-8 Myanmar text without the UTF-8
flag, so they are decoded from cp437 back to UTF-8. Members are written to
data/source/extracted/ under ASCII names (NAMES below) because the Myanmar
names break several Windows tools.

What the archive holds (all prepared 26-27 Sep 2026 by GSM from MIMU and
HydroSHEDS data, plus one Dawei Watch field report):

  * 655 MIMU village points (Pcode v9.5) in five townships, each screened for
    straight-line distance to HydroRIVERS mapped streams and HydroBASINS basin
    membership (Village_Screening_655.csv inside the inner zip);
  * the 197 points within 200 m, further graded by a visual DEM elevation class
    into three monitoring priorities (the 197-village KMZ);
  * 38 villages named as flooded / landslide-hit in Dawei Watch's 27 Sep 2026
    report, and 43 GSM priority-monitoring villages (the 27-9-2026 KML);
  * HydroBASINS polygons (Dawei 8, Tanintharyi River 11) and HydroRIVERS
    reaches for both basins.

The Kyainseikgyi (Kayin State) basin and stream files in the archive lie
150 km north of the study area and are not used by any of the screening
products; they are parsed only to report that fact.

Output: data/processed/source.json + basins.geojson + streams.geojson
"""

import csv
import io
import json
import os
import re
import zipfile

from lxml import etree
from shapely.geometry import LineString, MultiLineString, Polygon, mapping, shape
from shapely.ops import unary_union
from shapely.validation import make_valid

HERE = os.path.dirname(__file__)
ROOT = os.path.abspath(os.path.join(HERE, ".."))
ARCHIVE = os.path.join(ROOT, "data", "source", "tanintharyi_flood_9_2026.rar")
EXTRACT = os.path.join(ROOT, "data", "source", "extracted")
OUT = os.path.join(ROOT, "data", "processed")

# archive member (Myanmar/English base name) -> ASCII name on disk
NAMES = {
    "Basin Boundary_Tanintharyi River Basin.kmz": "basin_tanintharyi_river.kmz",
    "Basins_Dawei.kmz": "basins_dawei.kmz",
    "Basin_KISG.kmz": "basin_kyainseikgyi.kmz",
    "တနင်္သာရီမြို့နယ်.kml": "villages_tanintharyi.kml",
    "Stream Network_Dawei.kmz": "streams_dawei.kmz",
    "ထားဝယ် — သတင်းပါရွာ + ဦးစားပေးစောင့်ကြည့်ရွာ.kml": "reported_priority_dawei.kml",
    "655 Village Points — Stream_Basin Screening.kmz": "screening_655_v2.kmz",
    "Village_Stream_Basin_Screening_655.kmz": "screening_655.kmz",
    "ပုလော — သတင်းပါရွာ + ဦးစားပေးစောင့်ကြည့်ရွာ.kml": "reported_priority_palaw.kml",
    "Basin Bdy.kml": "basin_tanintharyi_river.kml",
    "Stream_Dawei.kml": "streams_dawei.kml",
    "Stream_Tanintharyi River Basin.kmz": "streams_tanintharyi_river.kmz",
    "Tanintharyi_655_Village_Screening.zip": "screening_655_package.zip",
    "တနင်္သာရီ — သတင်းပါရွာ + ဦးစားပေးစောင့်ကြည့်ရွာ.kml": "reported_priority_tanintharyi.kml",
    "သရက်ချောင်းမြို့နယ်.kml": "villages_thayetchaung.kml",
    "Tanintharyi_197_Village_Elevation_Stream_Screening.kmz": "screening_197_elevation.kmz",
    "သရက်ချောင်း — သတင်းပါရွာ + ဦးစားပေးစောင့်ကြည့်ရွာ.kml": "reported_priority_thayetchaung.kml",
    "Village_Screening_655.xlsx": "Village_Screening_655.xlsx",
    "လောင်းလုံမြို့နယ်.kml": "villages_launglon.kml",
    "Stream Network_KISG.kmz": "streams_kyainseikgyi.kmz",
    "Stream_Tanintharyi_River.kml": "streams_tanintharyi_river.kml",
    "ပုလောမြို့နယ်.kml": "villages_palaw.kml",
    "ထားဝယ်မြို့နယ်.kml": "villages_dawei.kml",
    "27-9-2026 မြို့နယ်အလိုက် သတင်းပါရွာ + GSM ဦးစားပေးရွာ.kml": "reported_and_gsm_priority_20260927.kml",
}

TS_MM_EN = {"ထားဝယ်": "Dawei", "လောင်းလုံး": "Launglon", "လောင်းလုံ": "Launglon",
            "သရက်ချောင်း": "Thayetchaung", "ပုလော": "Palaw", "တနင်္သာရီ": "Tanintharyi"}
HAZARD_EN = {"ရေကြီး": "Flood", "ရေကြီး/မြေပြို": "Flood / landslide",
             "ရေကြီး/လမ်းရေကျော်": "Flood / road overtopped"}
PARSER = etree.XMLParser(recover=True, huge_tree=True)


# ------------------------------------------------------------------ archive
def unpack():
    os.makedirs(EXTRACT, exist_ok=True)
    z = zipfile.ZipFile(ARCHIVE)
    seen = set()
    for info in z.infolist():
        name = info.filename
        if not info.flag_bits & 0x800:
            name = name.encode("cp437").decode("utf-8")
        if name.endswith("/"):
            continue
        # the archive spells ဦ as ဥ + ီ (U+1025 U+102E); fold it to U+1026
        base = name.split("/")[-1].replace("ဦ", "ဦ")
        if base not in NAMES:
            raise SystemExit(f"unexpected archive member: {base!r}")
        with open(os.path.join(EXTRACT, NAMES[base]), "wb") as f:
            f.write(z.read(info))
        seen.add(base)
    missing = set(NAMES) - seen
    if missing:
        raise SystemExit(f"archive is missing {missing}")
    print(f"unpacked {len(seen)} files -> data/source/extracted/")


def kml_root(name, inner=None):
    path = os.path.join(EXTRACT, name)
    if name.endswith(".kmz"):
        with zipfile.ZipFile(path) as z:
            data = z.read(inner or "doc.kml")
    else:
        data = open(path, "rb").read()
    return etree.fromstring(data, PARSER)


def local(el):
    return etree.QName(el).localname


def placemarks(root):
    """Yield (folder path, placemark element) in document order."""
    def rec(el, folders):
        for ch in el:
            tag = local(ch)
            if tag in ("Folder", "Document"):
                n = next((c.text for c in ch if local(c) == "name"), "") or ""
                yield from rec(ch, folders + [n])
            elif tag == "Placemark":
                yield folders, ch
    yield from rec(root, [])


def child_text(el, tag):
    for c in el:
        if local(c) == tag:
            return (c.text or "").strip()
    return ""


def coords(el):
    out = []
    for c in el.iter():
        if local(c) == "coordinates" and c.text:
            ring = []
            for tok in c.text.split():
                parts = tok.split(",")
                ring.append((float(parts[0]), float(parts[1])))
            out.append((c, ring))
    return out


def table_fields(desc):
    """Parse the ArcGIS-export popup table: <td>KEY</td><td>VALUE</td> pairs."""
    cells = re.findall(r"<td>(.*?)</td>", desc, re.S)
    cells = [re.sub("<[^>]+>", "", c).strip() for c in cells]
    return {cells[i]: cells[i + 1] for i in range(len(cells) - 1)
            if re.fullmatch(r"[A-Z_][A-Z0-9_]*", cells[i] or "")}


# ------------------------------------------------------------------ villages
def read_655():
    with zipfile.ZipFile(os.path.join(EXTRACT, "screening_655_package.zip")) as z:
        text = z.read("Village_Screening_655.csv").decode("utf-8-sig")
        summary = list(csv.DictReader(io.StringIO(z.read("Township_Summary.csv").decode("utf-8-sig"))))
        audit = json.loads(z.read("Computation_Audit.json"))
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        d = r["Mapped stream distance (m)"]
        rows.append(dict(
            pcode=r["Village Pcode"], ts=r["Township"], ts_mm=r["Township (MM)"],
            mm=r["Village (MM)"], en=r["Village (EN)"], vt_mm=r["Village tract (MM)"],
            lat=float(r["Latitude"]), lon=float(r["Longitude"]),
            basin=r["Basin group"], dist=round(float(d), 1) if d else None,
            cls=r["Distance class"], hybas=r["HYBAS_ID"] or None,
            strahler=int(r["Strahler order"]) if r["Strahler order"] else None,
            edge=1 if r["Basin-edge review"] else 0,
        ))
    return rows, summary, audit


def read_197():
    """Monitoring priority + elevation class for the 197 points within 200 m."""
    out = {}
    for folders, pm in placemarks(kml_root("screening_197_elevation.kmz")):
        desc = child_text(pm, "description")
        cells = dict(re.findall(r"<td><b>(.*?)</b></td><td>(?:<b>)?(.*?)(?:</b>)?</td>", desc))
        out[cells["P-code"]] = dict(
            priority=cells["Monitoring priority"], elev=cells["Elevation class"],
            dem_conf=round(float(cells["DEM confidence"]), 3) if cells["DEM confidence"] else None,
        )
    return out


def read_reports():
    """The 27 Sep 2026 KML: Dawei Watch reported villages + GSM priority villages."""
    rep, gsm = [], []
    for folders, pm in placemarks(kml_root("reported_and_gsm_priority_20260927.kml")):
        ts_mm = folders[1]
        name = child_text(pm, "name")
        desc = child_text(pm, "description")
        (_, pts), = coords(pm)
        lon, lat = pts[0]
        base = dict(mm=name, ts=TS_MM_EN[ts_mm], ts_mm=ts_mm, lon=lon, lat=lat)
        if "သတင်းပါ" in folders[2]:
            hz = re.search(r"ဖြစ်စဉ်:\s*([^<]+)", desc).group(1).strip()
            src = re.search(r"Source:\s*([^<]+)", desc).group(1).strip()
            rep.append(dict(base, hazard_mm=hz, hazard=HAZARD_EN[hz], source=src))
        else:
            gsm.append(base)
    return rep, gsm


# ------------------------------------------------------------------ hydrography
def polys(pm):
    rings = [r for _, r in coords(pm)]
    # outer/inner boundary order is preserved per Polygon element
    out = []
    for p in pm.iter():
        if local(p) != "Polygon":
            continue
        outer, inner = None, []
        for b in p:
            if local(b) == "outerBoundaryIs":
                outer = coords(b)[0][1]
            elif local(b) == "innerBoundaryIs":
                inner.append(coords(b)[0][1])
        out.append(Polygon(outer, inner))
    return unary_union([make_valid(g) for g in out]) if out else None


def read_basins():
    feats = []
    for fn, group in (("basins_dawei.kmz", "Dawei"), ("basin_tanintharyi_river.kmz", "Tanintharyi River")):
        for _, pm in placemarks(kml_root(fn)):
            a = table_fields(child_text(pm, "description"))
            g = polys(pm)
            main = a["MAIN_BAS"]
            label = {"4070022660": "Dawei main basin",
                     "4070022440": "Tanintharyi main basin"}.get(main, "Other / coastal basin")
            feats.append(dict(geometry=g, props=dict(
                hybas=a["HYBAS_ID"], main_bas=main, group=label, file_group=group,
                sub_km2=float(a["SUB_AREA"]), up_km2=float(a["UP_AREA"]),
                coast=int(a["COAST"]), order=int(a["ORDER_"]))))
    return feats


def read_streams():
    reaches = {}
    for fn in ("streams_dawei.kmz", "streams_tanintharyi_river.kmz"):
        for _, pm in placemarks(kml_root(fn)):
            a = table_fields(child_text(pm, "description"))
            lines = [LineString(r) for _, r in coords(pm) if len(r) > 1]
            rid = a["HYRIV_ID"]
            if rid in reaches:
                # the one reach clipped differently in the two files: keep the union
                reaches[rid]["lines"] += lines
                continue
            reaches[rid] = dict(lines=lines, props=dict(
                id=int(rid), ord=int(a["ORD_STRA"]), q=float(a["DIS_AV_CMS"]),
                up=float(a["UPLAND_SKM"]), len_km=float(a["LENGTH_KM"]),
                main=int(a["MAIN_RIV"])))
    return reaches


def bbox_of(fn):
    xs, ys = [], []
    for _, pm in placemarks(kml_root(fn)):
        for _, r in coords(pm):
            xs += [p[0] for p in r]
            ys += [p[1] for p in r]
    return [round(min(xs), 2), round(min(ys), 2), round(max(xs), 2), round(max(ys), 2)]


def rnd(obj, p):
    if isinstance(obj, (list, tuple)):
        if obj and isinstance(obj[0], (int, float)):
            return [round(float(v), p) for v in obj]
        return [rnd(v, p) for v in obj]
    return obj


def write_fc(name, feats, prec=4):
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": p,
         "geometry": {"type": g["type"], "coordinates": rnd(g["coordinates"], prec)}}
        for g, p in feats]}
    path = os.path.join(OUT, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(fc, f, ensure_ascii=False, separators=(",", ":"))
    print(f"  -> {name:22s} {len(feats):5,d} features {os.path.getsize(path)/1024:8.1f} KB")


def main():
    os.makedirs(OUT, exist_ok=True)
    unpack()

    v655, ts_summary, audit = read_655()
    p197 = read_197()
    for v in v655:
        v.update(p197.get(v["pcode"], {}))
    n197 = sum(1 for v in v655 if "priority" in v)
    assert n197 == 197 == sum(v["cls"] == "0-200 m" for v in v655), n197

    rep, gsm = read_reports()
    # GSM priority villages are the 197-screen's "Immediate Field Check" points:
    # match them back by coordinates (exact copies)
    by_xy = {(round(v["lon"], 5), round(v["lat"], 5)): v for v in v655}
    for g in gsm:
        v = by_xy.get((round(g["lon"], 5), round(g["lat"], 5)))
        if v is None:
            raise SystemExit(f"GSM priority village not in 655: {g}")
        g["pcode"] = v["pcode"]
        v["gsm"] = 1
    assert all(v.get("priority") == "Immediate Field Check" for v in v655 if v.get("gsm"))
    print(f"655 villages; 197 graded; {len(gsm)} GSM priority (all = Immediate Field Check); "
          f"{len(rep)} Dawei Watch reported villages")

    basins = read_basins()
    reaches = read_streams()
    kisg = bbox_of("basin_kyainseikgyi.kmz")
    print(f"basins {len(basins)}, stream reaches {len(reaches)}; Kyainseikgyi basin bbox {kisg} (unused)")

    write_fc("basins.geojson", [(mapping(b["geometry"].simplify(0.0005)), b["props"]) for b in basins])
    st = []
    for r in reaches.values():
        g = r["lines"][0] if len(r["lines"]) == 1 else unary_union(MultiLineString(r["lines"]))
        st.append((mapping(g), r["props"]))
    write_fc("streams.geojson", st)

    src = dict(villages=v655, reported=rep, gsm_priority=gsm, township_summary=ts_summary,
               audit=dict(k for k in audit.items() if k[0] in (
                   "created_label_date", "unique_hyriv_ids", "near_basin_edge_points",
                   "distance_crs", "threshold_warning", "limitations", "outside_points")),
               kyainseikgyi_bbox=kisg)
    with open(os.path.join(OUT, "source.json"), "w", encoding="utf-8") as f:
        json.dump(src, f, ensure_ascii=False, indent=0)
    print("  -> source.json")


if __name__ == "__main__":
    main()
