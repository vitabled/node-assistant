// Offline geo helpers for the «Хостинги» map. Fully client-side and
// self-contained — the backend persists lat/lng verbatim (no geocoding API),
// so these fill coordinates from a city+country when the operator hasn't typed
// them in by hand, and bucket locations into continents for the map filter.
// Coordinates are [lng, lat] (GeoJSON / react-simple-maps order).

export type ContinentKey = "EU" | "AS" | "AF" | "NA" | "SA" | "OC";

export const CONTINENTS: { key: ContinentKey; label: string }[] = [
  { key: "EU", label: "Европа" },
  { key: "AS", label: "Азия" },
  { key: "AF", label: "Африка" },
  { key: "NA", label: "Сев. Америка" },
  { key: "SA", label: "Юж. Америка" },
  { key: "OC", label: "Океания" },
];

// Recenter/zoom presets for the “focus continent” action.
export const CONTINENT_VIEW: Record<ContinentKey, { center: [number, number]; zoom: number }> = {
  EU: { center: [15, 52], zoom: 4 },
  AS: { center: [95, 35], zoom: 2 },
  AF: { center: [20, 3], zoom: 2.2 },
  NA: { center: [-100, 42], zoom: 2 },
  SA: { center: [-60, -20], zoom: 2.2 },
  OC: { center: [140, -25], zoom: 2.8 },
};
export const WORLD_VIEW: { center: [number, number]; zoom: number } = { center: [12, 22], zoom: 1 };

// Continent per ISO alpha-2 (only the codes the CountrySelect offers). Some
// transcontinental countries are bucketed by where their hosting is (RU/TR → EU,
// CY → EU) rather than strict geography.
export const CONTINENT: Record<string, ContinentKey> = {
  AL: "EU", AM: "AS", AR: "SA", AT: "EU", AU: "OC", AZ: "AS", BE: "EU", BG: "EU",
  BR: "SA", BY: "EU", CA: "NA", CH: "EU", CL: "SA", CN: "AS", CY: "EU", CZ: "EU",
  DE: "EU", DK: "EU", EE: "EU", ES: "EU", FI: "EU", FR: "EU", GB: "EU", GE: "AS",
  GR: "EU", HK: "AS", HR: "EU", HU: "EU", ID: "AS", IE: "EU", IL: "AS", IN: "AS",
  IR: "AS", IS: "EU", IT: "EU", JP: "AS", KZ: "AS", LT: "EU", LU: "EU", LV: "EU",
  MD: "EU", MX: "NA", MY: "AS", NL: "EU", NO: "EU", NZ: "OC", PL: "EU", PT: "EU",
  RO: "EU", RS: "EU", RU: "EU", SA: "AS", SE: "EU", SG: "AS", SI: "EU", SK: "EU",
  TH: "AS", TR: "EU", TW: "AS", UA: "EU", US: "NA", UZ: "AS", VN: "AS", ZA: "AF",
};

// One representative point per country (capital / major DC hub) so a marker can
// appear from the country alone when no city coords are available. [lng, lat].
export const COUNTRY_CENTROID: Record<string, [number, number]> = {
  AL: [19.82, 41.33], AM: [44.51, 40.18], AR: [-58.38, -34.60], AT: [16.37, 48.21],
  AU: [149.13, -35.28], AZ: [49.87, 40.41], BE: [4.35, 50.85], BG: [23.32, 42.70],
  BR: [-47.93, -15.78], BY: [27.57, 53.90], CA: [-79.38, 43.65], CH: [8.54, 47.37],
  CL: [-70.65, -33.46], CN: [116.41, 39.90], CY: [33.36, 35.17], CZ: [14.42, 50.09],
  DE: [8.68, 50.11], DK: [12.57, 55.68], EE: [24.75, 59.44], ES: [-3.70, 40.42],
  FI: [24.94, 60.17], FR: [2.35, 48.86], GB: [-0.13, 51.51], GE: [44.83, 41.72],
  GR: [23.73, 37.98], HK: [114.16, 22.32], HR: [15.98, 45.81], HU: [19.04, 47.50],
  ID: [106.85, -6.21], IE: [-6.26, 53.35], IL: [34.78, 32.08], IN: [77.21, 28.61],
  IR: [51.39, 35.69], IS: [-21.83, 64.13], IT: [12.50, 41.90], JP: [139.69, 35.69],
  KZ: [76.89, 43.24], LT: [25.28, 54.69], LU: [6.13, 49.61], LV: [24.11, 56.95],
  MD: [28.86, 47.01], MX: [-99.13, 19.43], MY: [101.69, 3.14], NL: [4.90, 52.37],
  NO: [10.75, 59.91], NZ: [174.76, -36.85], PL: [21.01, 52.23], PT: [-9.14, 38.72],
  RO: [26.10, 44.43], RS: [20.46, 44.79], RU: [37.62, 55.75], SA: [46.72, 24.71],
  SE: [18.07, 59.33], SG: [103.82, 1.35], SI: [14.51, 46.06], SK: [17.11, 48.15],
  TH: [100.50, 13.76], TR: [28.98, 41.01], TW: [121.56, 25.03], UA: [30.52, 50.45],
  US: [-77.49, 39.04], UZ: [69.24, 41.31], VN: [105.83, 21.03], ZA: [28.05, -26.20],
};

// Curated datacenter-city gazetteer. Key: `<cc-lower>:<normCity>`. [lng, lat].
const CITY_COORDS: Record<string, [number, number]> = {
  // Germany
  "de:frankfurt": [8.68, 50.11], "de:frankfurtammain": [8.68, 50.11],
  "de:nuremberg": [11.08, 49.45], "de:nurnberg": [11.08, 49.45],
  "de:falkenstein": [12.37, 50.48], "de:berlin": [13.40, 52.52], "de:munich": [11.58, 48.14],
  // Netherlands / Belgium / Luxembourg
  "nl:amsterdam": [4.90, 52.37], "be:brussels": [4.35, 50.85], "lu:luxembourg": [6.13, 49.61],
  // France
  "fr:paris": [2.35, 48.86], "fr:gravelines": [2.13, 50.99], "fr:strasbourg": [7.75, 48.58],
  "fr:roubaix": [3.18, 50.69], "fr:marseille": [5.37, 43.30],
  // UK / Ireland
  "gb:london": [-0.13, 51.51], "gb:manchester": [-2.24, 53.48], "ie:dublin": [-6.26, 53.35],
  // Nordics
  "fi:helsinki": [24.94, 60.17], "se:stockholm": [18.07, 59.33], "no:oslo": [10.75, 59.91],
  "dk:copenhagen": [12.57, 55.68], "is:reykjavik": [-21.83, 64.13],
  // Baltics
  "lt:vilnius": [25.28, 54.69], "lv:riga": [24.11, 56.95], "ee:tallinn": [24.75, 59.44],
  // Central / Eastern Europe
  "pl:warsaw": [21.01, 52.23], "cz:prague": [14.42, 50.09], "ro:bucharest": [26.10, 44.43],
  "hu:budapest": [19.04, 47.50], "bg:sofia": [23.32, 42.70], "rs:belgrade": [20.46, 44.79],
  "sk:bratislava": [17.11, 48.15], "si:ljubljana": [14.51, 46.06], "hr:zagreb": [15.98, 45.81],
  "ua:kyiv": [30.52, 50.45], "ua:kiev": [30.52, 50.45], "by:minsk": [27.57, 53.90],
  "md:chisinau": [28.86, 47.01], "al:tirana": [19.82, 41.33],
  // Southern Europe
  "ch:zurich": [8.54, 47.37], "ch:geneva": [6.14, 46.20], "at:vienna": [16.37, 48.21],
  "es:madrid": [-3.70, 40.42], "es:barcelona": [2.17, 41.39], "it:milan": [9.19, 45.46],
  "it:rome": [12.50, 41.90], "pt:lisbon": [-9.14, 38.72], "gr:athens": [23.73, 37.98],
  "cy:nicosia": [33.36, 35.17],
  // Russia / Turkey
  "ru:moscow": [37.62, 55.75], "ru:saintpetersburg": [30.34, 59.93], "ru:spb": [30.34, 59.93],
  "tr:istanbul": [28.98, 41.01],
  // USA
  "us:ashburn": [-77.49, 39.04], "us:newyork": [-74.01, 40.71], "us:losangeles": [-118.24, 34.05],
  "us:chicago": [-87.63, 41.88], "us:dallas": [-96.80, 32.78], "us:seattle": [-122.33, 47.61],
  "us:miami": [-80.19, 25.76], "us:sanjose": [-121.89, 37.34], "us:atlanta": [-84.39, 33.75],
  "us:portland": [-122.68, 45.52], "us:phoenix": [-112.07, 33.45],
  // Canada / Mexico / LatAm
  "ca:toronto": [-79.38, 43.65], "ca:montreal": [-73.57, 45.50], "ca:beauharnois": [-73.90, 45.31],
  "mx:mexicocity": [-99.13, 19.43], "br:saopaulo": [-46.63, -23.55], "ar:buenosaires": [-58.38, -34.60],
  "cl:santiago": [-70.65, -33.46],
  // Middle East / Caucasus / Central Asia
  "il:telaviv": [34.78, 32.08], "sa:riyadh": [46.72, 24.71], "ir:tehran": [51.39, 35.69],
  "ge:tbilisi": [44.83, 41.72], "am:yerevan": [44.51, 40.18], "az:baku": [49.87, 40.41],
  "kz:almaty": [76.89, 43.24], "uz:tashkent": [69.24, 41.31],
  // Asia-Pacific
  "sg:singapore": [103.82, 1.35], "jp:tokyo": [139.69, 35.69], "jp:osaka": [135.50, 34.69],
  "hk:hongkong": [114.16, 22.32], "cn:beijing": [116.41, 39.90], "cn:shanghai": [121.47, 31.23],
  "tw:taipei": [121.56, 25.03], "kr:seoul": [126.98, 37.57], "my:kualalumpur": [101.69, 3.14],
  "th:bangkok": [100.50, 13.76], "vn:hanoi": [105.83, 21.03], "vn:hochiminh": [106.66, 10.82],
  "id:jakarta": [106.85, -6.21], "in:mumbai": [72.88, 19.08], "in:bangalore": [77.59, 12.97],
  "in:delhi": [77.21, 28.61],
  // Oceania / Africa
  "au:sydney": [151.21, -33.87], "au:melbourne": [144.96, -37.81], "nz:auckland": [174.76, -36.85],
  "za:johannesburg": [28.05, -26.20], "za:capetown": [18.42, -33.92],
};

export function normCity(s: string): string {
  return (s || "")
    .normalize("NFD").replace(/[̀-ͯ]/g, "")   // strip diacritics
    .toLowerCase().replace(/[^a-z0-9]/g, "");
}

/** Resolve [lng,lat] from a city+country (city match first, else country point). */
export function resolveCoords(countryCode: string, city: string): [number, number] | null {
  const cc = (countryCode || "").toLowerCase();
  const key = `${cc}:${normCity(city)}`;
  if (CITY_COORDS[key]) return CITY_COORDS[key];
  const up = (countryCode || "").toUpperCase();
  return COUNTRY_CENTROID[up] ?? null;
}

export function continentOf(countryCode: string): ContinentKey | null {
  return CONTINENT[(countryCode || "").toUpperCase()] ?? null;
}
