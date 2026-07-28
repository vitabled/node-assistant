// Ширина сетевого канала тарифа: разбор строки → Мбит/с, цвет ступени и
// логарифмическая шкала полоски. Чистый модуль по тем же соображениям, что и
// `metrics.ts`/`search.ts`: разбор пользовательского текста надо проверять без
// рендера.
//
// Парсер нужен потому, что `Tariff.bandwidth` — СВОБОДНЫЙ ТЕКСТ (провайдер
// пишет порт, гарантию и лимит трафика одной строкой: «1 Гбит/с, 20 ТБ»), и
// менять модель нельзя — данные уже введены.

/**
 * Кириллица → латиница: единицы пишут в обеих раскладках («Гбит» = «Gbit»).
 * Транслитерация всей азбуки, а не только буквенных единиц — иначе «байт»
 * пришлось бы ловить отдельным правилом.
 */
const CYR2LAT: Record<string, string> = {
  а: "a", б: "b", в: "v", г: "g", д: "d", е: "e", ё: "e", ж: "zh", з: "z",
  и: "i", й: "y", к: "k", л: "l", м: "m", н: "n", о: "o", п: "p", р: "r",
  с: "s", т: "t", у: "u", ф: "f", х: "h", ц: "c", ч: "ch", ш: "sh", щ: "sch",
  ъ: "", ы: "y", ь: "", э: "e", ю: "yu", я: "ya",
};

function normalize(text: string): string {
  let out = "";
  for (const ch of (text || "").toLowerCase()) out += CYR2LAT[ch] ?? ch;
  // Десятичную запятую («2,5 Гбит») чиним только между цифрами, чтобы не
  // склеить перечисление «10 Гбит/с, 100 ТБ» в одно число.
  return out.replace(/(\d),(\d)/g, "$1.$2");
}

/** Хвост «в секунду»: «/с», «/сек», «ps», «s» — после транслитерации. */
const PS = String.raw`(?:\/(?:s|sec|sek)|ps|s)?`;

/** Единицы СКОРОСТИ → множитель к Мбит/с. */
const SPEED_UNITS: Array<[RegExp, number]> = [];
for (const [p, mult] of [["t", 1e6], ["g", 1e3], ["m", 1], ["k", 1e-3]] as const) {
  SPEED_UNITS.push(
    [new RegExp(`^${p}bit${PS}$`), mult],               // гбит, gbit/s, mbit/sec
    [new RegExp(`^${p}bps$`), mult],                    // gbps, mbps
    [new RegExp(`^${p}(?:\\/(?:s|sec|sek))?$`), mult],  // «10G», «1000M», «1G/s»
    // «Гб/с» буквально — гигаБАЙТЫ в секунду, но в прайсах хостеров так всегда
    // пишут гигабиты. Требуем явное «в секунду»: именно оно отличает такую
    // запись от объёма трафика «20 ГБ».
    [new RegExp(`^${p}b\\/(?:s|sec|sek)$`), mult],
  );
}

/**
 * Объём трафика — НЕ скорость: «20 ТБ», «100 GB». Такой токен пропускаем и
 * ищем дальше, потому что скорость может стоять рядом («20 ТБ, 1 Гбит/с»).
 */
const BYTE_UNIT = /^[tgmk]?(?:b|byte|bytes|bayt)$/;

/**
 * Ширина канала в Мбит/с или `null`, если в строке её нет. Голое число без
 * единицы — тоже `null`: «1000» одинаково похоже на мегабиты и на гигабайты,
 * а показать исходный текст честнее, чем угадать.
 */
export function parseChannel(text: string): number | null {
  for (const m of normalize(text).matchAll(/(\d+(?:\.\d+)?)\s*([a-z/]*)/g)) {
    const value = parseFloat(m[1]);
    if (!Number.isFinite(value) || value <= 0) continue;
    if (BYTE_UNIT.test(m[2])) continue;
    const hit = SPEED_UNITS.find(([re]) => re.test(m[2]));
    if (hit) return value * hit[1];
  }
  return null;
}

/**
 * Цвет ступени: до 150 Мбит/с, до гигабита, до 10G и выше. Берём токены
 * data-ink-рампы, а не свои оттенки — их подкручивает неон-скин.
 */
export function channelColor(mbps: number | null | undefined): string {
  if (mbps == null || !Number.isFinite(mbps)) return "var(--t-low)";
  if (mbps <= 150) return "var(--viz-3)";
  if (mbps <= 1000) return "var(--viz-1)";
  if (mbps <= 10000) return "var(--viz-2)";
  return "var(--viz-5)";
}

const PCT_MIN_MBPS = 100;    // левый край шкалы
const PCT_MAX_MBPS = 25000;  // правый край
const PCT_LO = Math.log10(PCT_MIN_MBPS);
const PCT_SPAN = Math.log10(PCT_MAX_MBPS) - PCT_LO;

/**
 * Ширина полоски в процентах. Шкала логарифмическая: от 100 Мбит до 25 Гбит
 * разница в 250 раз, на линейной всё до гигабита слиплось бы у нуля.
 * Нижний зажим 6% — чтобы самый узкий канал остался полоской, а не точкой.
 */
export function channelPct(mbps: number): number {
  if (!Number.isFinite(mbps)) return 6;
  const raw = ((Math.log10(Math.max(mbps, PCT_MIN_MBPS)) - PCT_LO) / PCT_SPAN) * 100;
  return Math.min(100, Math.max(6, raw));
}

/** «100 Мбит/с» · «1 Гбит/с» · «2.5 Гбит/с» — дробная часть только у некруглых. */
export function fmtChannel(mbps: number): string {
  const gig = mbps >= 1000;
  const v = gig ? mbps / 1000 : mbps;
  return `${Number.isInteger(v) ? v : v.toFixed(1)} ${gig ? "Гбит/с" : "Мбит/с"}`;
}

/**
 * Компактная подпись для сегмента полоски: «100М», «1Г», «2.5Г».
 * В карточке под сегментом бывает 30-40 пикселей — полная форма «1 Гбит/с»
 * туда не влезает и обрезается многоточием, что читается хуже короткой.
 */
export function fmtChannelShort(mbps: number): string {
  const gig = mbps >= 1000;
  const v = gig ? mbps / 1000 : mbps;
  return `${Number.isInteger(v) ? v : v.toFixed(1)}${gig ? "Г" : "М"}`;
}
