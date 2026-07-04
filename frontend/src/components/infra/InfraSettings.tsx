import { useState, useEffect } from "react";
import { SlidersHorizontal, Loader2, Save, CheckCircle2 } from "lucide-react";
import { infraApi, type BillingSettings } from "./api";
import { toast } from "./Toast";
import { Page, PageHeader, Field, SelectField, inputCls } from "./ui";

const CURRENCIES = ["RUB", "USD", "EUR"];

export function InfraSettings() {
  const [s, setS] = useState<BillingSettings | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => { infraApi.getSettings().then(setS).catch(e => toast((e as Error).message, "error")); }, []);

  const save = async () => {
    if (!s) return;
    setSaving(true);
    const body: Record<string, unknown> = {
      base_currency: s.baseCurrency,
      fx_rates: s.fxRates,
      low_balance_threshold: s.lowBalanceThreshold,
      refresh_interval: s.refreshInterval,
    };
    try {
      await infraApi.putSettings(body);
      setSaved(true); setTimeout(() => setSaved(false), 2000);
      setS(await infraApi.getSettings());
    } catch (e) { toast((e as Error).message, "error"); }
    setSaving(false);
  };

  if (!s) return <Page><div className="py-16 text-center text-gray-600"><Loader2 size={18} className="animate-spin inline" /></div></Page>;

  const setRate = (cur: string, val: string) =>
    setS({ ...s, fxRates: { ...s.fxRates, [cur]: parseFloat(val) || 0 } });

  return (
    <Page>
      <PageHeader icon={<SlidersHorizontal size={16} className="text-blue-400" />} title="Настройки биллинга"
        subtitle="Базовая валюта, курсы и пороги" />

      <div className="max-w-lg flex flex-col gap-5">
        <SelectField label="Базовая валюта системы" value={s.baseCurrency}
          onChange={v => setS({ ...s, baseCurrency: v })} options={CURRENCIES.map(c => ({ v: c, l: c }))} />

        <div>
          <label className="text-[11px] font-medium text-gray-500 uppercase tracking-widest">Курсы (1 единица = X RUB)</label>
          <div className="grid grid-cols-3 gap-3 mt-1">
            {CURRENCIES.map(c => (
              <div key={c} className="flex flex-col gap-1">
                <span className="text-[11px] text-gray-600">{c}</span>
                <input type="number" value={String(s.fxRates[c] ?? "")} onChange={e => setRate(c, e.target.value)} className={inputCls} />
              </div>
            ))}
          </div>
          <p className="text-[11px] text-gray-600 mt-1">Курсы вводятся вручную (авто-обновление FX не подключено).</p>
        </div>

        <Field label="Порог алерта баланса (в базовой валюте)" value={String(s.lowBalanceThreshold)}
          onChange={v => setS({ ...s, lowBalanceThreshold: parseFloat(v) || 0 })} type="number" />

        <SelectField label="Интервал авто-обновления балансов" value={s.refreshInterval}
          onChange={v => setS({ ...s, refreshInterval: v })}
          options={[{ v: "hourly", l: "Раз в час" }, { v: "daily", l: "Раз в сутки" }]} />

        <button onClick={save} disabled={saving}
          className="self-start flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50">
          {saved ? <><CheckCircle2 size={14} /> Сохранено</> : saving ? <><Loader2 size={14} className="animate-spin" /> Сохранение…</> : <><Save size={14} /> Сохранить</>}
        </button>
      </div>
    </Page>
  );
}
