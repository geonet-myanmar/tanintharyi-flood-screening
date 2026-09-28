# Tanintharyi Flood Screening, September 2026

Interactive dashboard for five townships of Tanintharyi Region, Myanmar (Dawei,
Launglon, Thayetchaung, Palaw, Tanintharyi). It sets the Geographical Society of
Myanmar's (GSM) village–stream screening of 655 MIMU villages against the 38
villages Dawei Watch reported flooded or hit by landslides on 27 September 2026,
on a basemap drawn from MIMU layers.

Open `index.html`. It is self-contained (data inline; Leaflet from cdnjs).

## On the page

- Headline figures and five findings, all computed from the data at build time.
- Map: reported villages (by hazard), screened villages coloured by stream
  distance, GSM monitoring priority or basin; HydroRIVERS streams, HydroBASINS
  basins; MIMU township and village-tract boundaries, towns, wards, roads,
  railway, bridges, road–stream crossings, schools, hospitals, land below 5 m.
  Village list with township/filter/search and a detail card per village.
- Charts: reported vs Immediate-check villages per township, report rate by
  distance class, distance strip plot, distance/priority/basin by township,
  hazards, road–stream crossings; tables of townships, reported villages and
  exposed facilities.

## Rebuild

```
pip install -r requirements.txt
python scripts/01_download_mimu.py   # MIMU GeoServer WFS -> data/raw/ (gitignored)
python scripts/02_parse_source.py    # archive -> data/processed/source.json, basins, streams
python scripts/03_process_gis.py     # MIMU join + analysis -> data/processed/
python scripts/04_build_html.py      # src/template.html + data -> index.html, dist/
```

Never hand-edit `index.html`; edit `src/template.html` and re-run script 04.

## Source notes

- `data/source/tanintharyi_flood_9_2026.rar` is really a ZIP with UTF-8 Myanmar
  member names; script 02 unpacks it to ASCII names in `data/source/extracted/`.
- The archive's Kyainseikgyi (Kayin State) basin/stream files are outside the
  study area and unused.
- 200 m / 500 m are GIS filters, not flood limits. No rainfall, river-level or
  inundation data is used.
