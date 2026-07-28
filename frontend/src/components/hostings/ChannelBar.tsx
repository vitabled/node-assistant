// Шкала ширины канала. Два вида:
//  • ChannelBar — строка ОДНОГО тарифа (таблица в деталях): подпись + полоска;
//  • ChannelStrip — карточка хостинга: одна полоска, разбитая на сегменты по
//    числу тарифов, цвет сегмента — по ширине канала этого тарифа.
// Презентационные компоненты — вся логика в `channel.ts`.
import { parseChannel, channelColor, channelPct, fmtChannel, fmtChannelShort } from "./channel";
import type { Tariff } from "./api";

export function ChannelBar({ text }: { text: string }) {
  const raw = (text || "").trim();
  if (!raw) return null;

  const mbps = parseChannel(raw);
  // Неразобранную строку показываем как есть: в поле пишут что угодно, и
  // потерять её хуже, чем не нарисовать шкалу.
  if (mbps === null) return <span className="text-[11px] text-[var(--t-low)]">{raw}</span>;

  const color = channelColor(mbps);
  return (
    // title — исходная строка целиком: в ней, кроме скорости, обычно ещё и
    // лимит трафика («10 Гбит/с, 100 ТБ»), который подпись не показывает.
    <div className="flex flex-col gap-1" title={raw}>
      <span className="num text-[11px]" style={{ color }}>{fmtChannel(mbps)}</span>
      {/* `--bg-soft` в наборе токенов проекта нет — дорожка падает на `--bg3`,
          как у полосы прогресса деплоя. */}
      <div style={{ height: 7, borderRadius: 4, overflow: "hidden", background: "var(--bg-soft, var(--bg3))" }}>
        <div style={{ width: `${channelPct(mbps)}%`, height: "100%", background: color }} />
      </div>
    </div>
  );
}

/**
 * Полоска каналов всего хостинга: столько сегментов, сколько у него тарифов.
 * Полоска сверху, подписи — под ней (так просил пользователь): глаз сначала
 * ловит цветовую картину провайдера целиком, а цифры читает при необходимости.
 *
 * Сегменты РАВНОЙ ширины, а не пропорциональные скорости: они отвечают на
 * вопрос «сколько тарифов и какие у них каналы», и узкий тариф не должен
 * съёживаться в невидимую полоску. Скорость передана цветом и подписью.
 */
export function ChannelStrip({ tariffs }: { tariffs: Tariff[] }) {
  const list = tariffs || [];
  if (list.length === 0) return null;

  const segs = list.map(t => {
    const raw = (t.bandwidth || "").trim();
    const mbps = parseChannel(raw);
    return { raw, mbps, name: (t.name || "").trim() };
  });
  // Ни у одного тарифа нет распознанного канала — рисовать серую пустую ленту
  // незачем, она не несёт информации.
  if (segs.every(s => s.mbps === null)) return null;

  const label = (s: typeof segs[number]) =>
    [s.name, s.raw || "канал не указан"].filter(Boolean).join(" · ");

  return (
    <div className="flex flex-col gap-1">
      <div className="flex gap-[2px]" style={{ height: 7 }}>
        {segs.map((s, i) => (
          <div key={i} title={label(s)} className="flex-1 min-w-0"
            style={{
              borderRadius: 2,
              // Нераспознанный канал — приглушённая дорожка: место тарифа в
              // ряду видно, но цветом ступени он не притворяется.
              background: s.mbps === null ? "var(--bg-soft, var(--bg3))" : channelColor(s.mbps),
              opacity: s.mbps === null ? 0.6 : 1,
            }} />
        ))}
      </div>
      <div className="flex gap-[2px] text-[10px] leading-none num">
        {segs.map((s, i) => (
          <span key={i} title={label(s)}
            className="flex-1 min-w-0 truncate text-center"
            style={{ color: s.mbps === null ? "var(--t-faint)" : channelColor(s.mbps) }}>
            {s.mbps === null ? "—" : fmtChannelShort(s.mbps)}
          </span>
        ))}
      </div>
    </div>
  );
}
