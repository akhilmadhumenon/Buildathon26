# Climate screen for ten Karnataka crops

Two districts: Kolar and Kalaburagi. Normals are the 2015–2024 mean temperature and mean annual rainfall from Open-Meteo.
Temperature and rainfall shifts are the mean of WorldClim CMIP6 MIROC6 and MPI-ESM1-2-HR at the 10-arc-minute cell over the district, 2041–2060 minus the WorldClim 1970–2000 baseline. That temperature delta is added to the Open-Meteo 2015–2024 normal, and rainfall is multiplied by the WorldClim ratio. The two cells warm by about 0.8°C to 1.2°C, which is milder than a blanket +1.5°C to +2.5°C slogan. This is two models at one cell, not a full ensemble raster in the app.

Score = 0.6 × temperature limb + 0.4 × rainfall limb. A limb is 100 inside the EcoCrop optimal band, 0 outside the absolute band, and linear between them.
Limits are FAO EcoCrop species parameters shipped with the R package dismo (`ECOcrops`). Dry chilli uses Capsicum annuum. Banana uses the cultivated Musa hybrid. Paddy uses indica rice. These are species screens, not variety advice, and they were not checked against a soil grid or against historical sown area.

## Kolar

Normal mean temperature 24.11°C. Normal annual rainfall 897.5 mm.

| Crop | Today | Moderate 2041–2060 | High 2041–2060 | Change under high |
| --- | --- | --- | --- | --- |
| Tomato | 100.0 | 100.0 | 100.0 | 0.0 |
| Dry chilli | 100.0 | 100.0 | 100.0 | 0.0 |
| Ragi | 100.0 | 100.0 | 100.0 | 0.0 |
| Maize | 100.0 | 100.0 | 100.0 | 0.0 |
| Tur | 100.0 | 100.0 | 100.0 | 0.0 |
| Groundnut | 100.0 | 100.0 | 100.0 | 0.0 |
| Potato | 96.8 | 95.0 | 91.8 | -5.0 |
| Onion | 94.6 | 93.6 | 90.3 | -4.3 |
| Banana | 54.7 | 59.3 | 60.0 | 5.3 |
| Paddy | 54.1 | 59.3 | 60.0 | 5.9 |

Highest today: Tomato (100.0), Dry chilli (100.0), Ragi (100.0).
Largest drop under the high screen: Potato (-5.0), Onion (-4.3).

## Kalaburagi

Normal mean temperature 27.17°C. Normal annual rainfall 926.1 mm.

| Crop | Today | Moderate 2041–2060 | High 2041–2060 | Change under high |
| --- | --- | --- | --- | --- |
| Dry chilli | 100.0 | 100.0 | 100.0 | 0.0 |
| Maize | 100.0 | 100.0 | 100.0 | 0.0 |
| Tur | 100.0 | 100.0 | 100.0 | 0.0 |
| Groundnut | 100.0 | 100.0 | 100.0 | 0.0 |
| Tomato | 98.7 | 91.8 | 89.9 | -8.8 |
| Ragi | 98.7 | 91.8 | 89.9 | -8.8 |
| Potato | 69.8 | 58.0 | 53.9 | -15.9 |
| Onion | 68.0 | 56.6 | 53.0 | -15.0 |
| Banana | 60.0 | 60.0 | 60.0 | 0.0 |
| Paddy | 60.0 | 60.0 | 60.0 | 0.0 |

Highest today: Dry chilli (100.0), Maize (100.0), Tur (100.0).
Largest drop under the high screen: Potato (-15.9), Onion (-15.0), Tomato (-8.8).

Paddy's rainfall band starts high, so a rainfed screen marks the interior districts down. Irrigated paddy is outside what this score can see.

When a farmer marks the load in transit or completes the sale, the line speaks this screen for their crop against drought-hardy ragi and tur, before the next sowing. It is still a species screen, not a variety recommendation.
