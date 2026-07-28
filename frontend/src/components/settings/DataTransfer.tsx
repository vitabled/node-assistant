import { useState, useEffect } from "react";
import { Download, Upload, Loader2, AlertTriangle, Send, Save, ListChecks } from "lucide-react";
import { toast } from "../infra/Toast";
import { buildGroups, type StoreGroup } from "./storePicker";

/** Общий список чекбоксов по группам — одна разметка для экспорта и импорта. */
function StoreChecklist({ groups, picked, onToggle, onAll, disabled }: {
  groups: StoreGroup[];
  picked: Set<string>;
  onToggle: (id: string) => void;
  onAll: (ids: string[] | null) => void;
  disabled?: boolean;
}) {
  const all = groups.flatMap(g => g.items.map(i => i.id));
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2 text-[11px]">
        <ListChecks size={12} className="text-[var(--t-low)]" />
        <button className="text-[var(--accent-hi)]" disabled={disabled}
          onClick={() => onAll(all)}>Выбрать всё</button>
        <span className="text-[var(--t-faint)]">·</span>
        <button className="text-[var(--accent-hi)]" disabled={disabled}
          onClick={() => onAll(null)}>Снять всё</button>
        <span className="text-[var(--t-faint)] ml-auto">выбрано {picked.size} из {all.length}</span>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-2">
        {groups.map(g => (
          <div key={g.title} className="flex flex-col gap-1">
            <span className="label">{g.title}</span>
            {g.items.map(i => (
              <label key={i.id} className="flex items-center gap-2 text-xs text-[var(--t-mid)] cursor-pointer">
                <input type="checkbox" checked={picked.has(i.id)} disabled={disabled}
                  onChange={() => onToggle(i.id)} style={{ accentColor: "var(--accent)" }} />
                <span className="truncate" title={i.id}>{i.label}</span>
              </label>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

// Wave-5 Plan L (slice 1) — export/import the account's node-assistant data.
export function DataTransfer() {
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [report, setReport] = useState<{ applied?: Record<string, number>; skipped?: string[] } | null>(null);

  // Что вообще можно выгрузить — спрашиваем у бэкенда, чтобы список не
  // расходился с ним при добавлении нового стора.
  const [groups, setGroups] = useState<StoreGroup[]>([]);
  const [pickedExp, setPickedExp] = useState<Set<string>>(new Set());
  const [pickAll, setPickAll] = useState(true);

  // Импорт: сначала смотрим, ЧТО внутри архива, и только потом выбираем.
  const [file, setFile] = useState<File | null>(null);
  const [inGroups, setInGroups] = useState<StoreGroup[]>([]);
  const [pickedImp, setPickedImp] = useState<Set<string>>(new Set());
  const [peekSecrets, setPeekSecrets] = useState(false);

  useEffect(() => {
    fetch("/api/export/stores").then(r => r.json())
      .then(d => setGroups(buildGroups(d.stores || [], d.settings_sections || [])))
      .catch(() => setGroups([]));
  }, []);

  const toggle = (set: Set<string>, put: (s: Set<string>) => void) => (id: string) => {
    const next = new Set(set);
    next.has(id) ? next.delete(id) : next.add(id);
    put(next);
  };

  const doExport = async () => {
    setBusy(true);
    try {
      // Пустой выбор = весь аккаунт (прежнее поведение и дефолт).
      const body = pickAll || pickedExp.size === 0 ? {} : { stores: [...pickedExp] };
      const r = await fetch("/api/export", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error();
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = "node-assistant-export.tar.gz"; a.click();
      URL.revokeObjectURL(url);
      toast("Экспорт скачан", "success");
    } catch { toast("Не удалось экспортировать", "error"); }
    finally { setBusy(false); }
  };

  const peek = async (f: File) => {
    setBusy(true); setReport(null);
    try {
      const fd = new FormData();
      fd.append("file", f);
      const r = await fetch("/api/import/peek", { method: "POST", body: fd });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || "Не удалось прочитать архив");
      const g = buildGroups(d.stores || [], d.settings_sections || []);
      setFile(f); setInGroups(g); setPeekSecrets(!!d.include_secrets);
      setPickedImp(new Set(g.flatMap(x => x.items.map(i => i.id))));
    } catch (e) {
      setFile(null); setInGroups([]);
      toast(e instanceof Error ? e.message : "Не удалось прочитать архив", "error");
    } finally { setBusy(false); }
  };

  const doImport = async (file: File) => {
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("confirm", "true");
      // Пусто = применить всё из архива; иначе только выбранное.
      const total = inGroups.flatMap(g => g.items).length;
      if (pickedImp.size && pickedImp.size < total) fd.append("stores", [...pickedImp].join(","));
      const r = await fetch("/api/import", { method: "POST", body: fd });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail || "Ошибка импорта");
      setReport(data);
      toast("Импорт выполнен", "success");
    } catch (e) { toast(e instanceof Error ? e.message : "Ошибка импорта", "error"); }
    finally { setBusy(false); }
  };

  return (
    <div className="flex flex-col gap-4 max-w-2xl">
      <div className="card card-p flex flex-col gap-3">
        <span className="text-sm font-semibold text-[var(--t-hi)]">Экспорт данных node-assistant</span>
        <p className="hint">Архив (.tar.gz) с настройками, шаблонами, правилами, хостами, подписками и т.д. Секреты (токены/ключи) исключаются.</p>

        <div className="seg mini accent" style={{ alignSelf: "flex-start" }}>
          <button className={pickAll ? "on" : ""} onClick={() => setPickAll(true)}>Всё</button>
          <button className={pickAll ? "" : "on"} onClick={() => setPickAll(false)}>Выбрать данные</button>
        </div>

        {!pickAll && (groups.length > 0
          ? <StoreChecklist groups={groups} picked={pickedExp} disabled={busy}
              onToggle={toggle(pickedExp, setPickedExp)}
              onAll={ids => setPickedExp(new Set(ids || []))} />
          : <p className="hint">Список данных не загрузился — экспорт выгрузит всё.</p>)}

        {!pickAll && pickedExp.size === 0 && (
          <p className="hint">Ничего не выбрано — будет выгружено всё.</p>
        )}

        <p className="hint">
          В архив попадают только JSON-данные. НЕ попадают: история мониторинга и статистики
          (SQLite), файлы Библиотеки и медиа, карточки деплоя (они в браузере).
        </p>

        <button className="btn btn-primary" disabled={busy} onClick={doExport} style={{ alignSelf: "flex-start" }}>
          {busy ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />} Экспортировать
        </button>
      </div>

      <div className="card card-p flex flex-col gap-3">
        <span className="text-sm font-semibold text-[var(--t-hi)]">Импорт</span>
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-[var(--warn-dim)] border border-[var(--warn-line)] text-[var(--warn)] text-xs">
          <AlertTriangle size={14} className="shrink-0" /> Импорт перезаписывает соответствующие данные аккаунта. Учётные секции (токены панелей/ключи) не затрагиваются.
        </div>
        <label className="flex items-center gap-2 text-sm text-[var(--t-mid)] cursor-pointer select-none">
          <input type="checkbox" checked={confirm} onChange={e => setConfirm(e.target.checked)} />
          Понимаю — перезаписать данные
        </label>
        <label className="btn" style={{ alignSelf: "flex-start", opacity: confirm && !busy ? 1 : 0.5, cursor: confirm && !busy ? "pointer" : "not-allowed" }}>
          <Upload size={14} /> Выбрать архив…
          <input type="file" accept=".gz,.tar.gz,application/gzip" style={{ display: "none" }} disabled={!confirm || busy}
            onChange={e => { const f = e.target.files?.[0]; if (f) peek(f); e.currentTarget.value = ""; }} />
        </label>

        {file && (
          <>
            <p className="hint">
              В архиве «{file.name}»{peekSecrets ? " (с секретами)" : ""} — отметьте, что применить:
            </p>
            <StoreChecklist groups={inGroups} picked={pickedImp} disabled={busy}
              onToggle={toggle(pickedImp, setPickedImp)}
              onAll={ids => setPickedImp(new Set(ids || []))} />
            <div className="flex items-center gap-2">
              <button className="btn btn-primary" disabled={busy || pickedImp.size === 0}
                onClick={() => doImport(file)}>
                {busy ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />} Импортировать выбранное
              </button>
              <button className="btn" disabled={busy}
                onClick={() => { setFile(null); setInGroups([]); }}>Отмена</button>
            </div>
          </>
        )}
        {report && (
          <p className="hint">
            Применено: {Object.keys(report.applied || {}).join(", ") || "—"}
            {report.skipped?.length ? ` · пропущено: ${report.skipped.join(", ")}` : ""}
          </p>
        )}
      </div>

      <AutoBackupCard />
    </div>
  );
}

// ── Wave-8 §4 — scheduled auto-backup → Telegram ───────────────
interface AbCfg {
  enabled: boolean;
  interval_hours: number;
  include_secrets: boolean;
  chat_id: string;
  has_token: boolean;
  last_run: number;
  last_error: string;
}

function AutoBackupCard() {
  const [cfg, setCfg] = useState<AbCfg>({
    enabled: false, interval_hours: 24, include_secrets: false,
    chat_id: "", has_token: false, last_run: 0, last_error: "",
  });
  const [token, setToken] = useState("");   // write-only; blank = keep existing
  const [saving, setSaving] = useState(false);
  const [sending, setSending] = useState(false);

  const load = async () => {
    try {
      const d = await fetch("/api/settings/auto-backup").then(r => r.json());
      setCfg(c => ({ ...c, ...d }));
    } catch { /* keep defaults */ }
  };
  useEffect(() => { load(); }, []);

  const save = async () => {
    setSaving(true);
    try {
      const r = await fetch("/api/settings/auto-backup", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          enabled: cfg.enabled, interval_hours: cfg.interval_hours,
          include_secrets: cfg.include_secrets, chat_id: cfg.chat_id,
          bot_token: token,   // "" → backend keeps the stored token
        }),
      });
      if (!r.ok) throw new Error();
      setToken("");
      await load();
      toast("Настройки автобэкапа сохранены", "success");
    } catch { toast("Не удалось сохранить", "error"); }
    finally { setSaving(false); }
  };

  const sendNow = async () => {
    setSending(true);
    try {
      const r = await fetch("/api/settings/auto-backup/run", { method: "POST" });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || "Ошибка отправки");
      toast("Бэкап отправлен в Telegram", "success");
      await load();
    } catch (e) { toast(e instanceof Error ? e.message : "Ошибка отправки", "error"); }
    finally { setSending(false); }
  };

  return (
    <div className="card card-p flex flex-col gap-3">
      <span className="text-sm font-semibold text-[var(--t-hi)]">Автобэкап → Telegram</span>
      <p className="hint">Периодическая отправка полного экспорта аккаунта в Telegram-чат через вашего бота.</p>

      <label className="flex items-center gap-2 text-sm text-[var(--t-mid)] cursor-pointer select-none">
        <input type="checkbox" checked={cfg.enabled} onChange={e => setCfg(c => ({ ...c, enabled: e.target.checked }))} />
        Включить автобэкап
      </label>

      <div className="grid grid-cols-2 gap-3">
        <div className="flex flex-col gap-1">
          <label className="label">Интервал (часов)</label>
          <input type="number" min={1} max={8760} value={cfg.interval_hours}
            onChange={e => setCfg(c => ({ ...c, interval_hours: parseInt(e.target.value) || 24 }))}
            className="input" />
        </div>
        <div className="flex flex-col gap-1">
          <label className="label">Chat ID</label>
          <input value={cfg.chat_id} onChange={e => setCfg(c => ({ ...c, chat_id: e.target.value }))}
            placeholder="напр. 123456789" spellCheck={false} className="input" />
        </div>
      </div>

      <div className="flex flex-col gap-1">
        <label className="label">Bot token</label>
        <input type="password" value={token} onChange={e => setToken(e.target.value)}
          placeholder={cfg.has_token ? "•••••••• (токен сохранён — оставьте пустым)" : "123456:AA…"}
          autoComplete="off" spellCheck={false} className="input" />
      </div>

      <label className="flex items-center gap-2 text-sm text-[var(--t-mid)] cursor-pointer select-none">
        <input type="checkbox" checked={cfg.include_secrets}
          onChange={e => setCfg(c => ({ ...c, include_secrets: e.target.checked }))} />
        Включать секреты (токены/ключи)
      </label>
      {cfg.include_secrets && (
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-[var(--warn-dim)] border border-[var(--warn-line)] text-[var(--warn)] text-xs">
          <AlertTriangle size={14} className="shrink-0" /> Секреты попадут в Telegram-чат в открытом виде — используйте только приватный чат.
        </div>
      )}

      {cfg.last_error
        ? <p className="hint text-[var(--err)]">Последняя ошибка: {cfg.last_error}</p>
        : cfg.last_run > 0 && <p className="hint">Последний бэкап: {new Date(cfg.last_run * 1000).toLocaleString()}</p>}

      <div className="flex items-center gap-2">
        <button className="btn btn-primary" disabled={saving} onClick={save}>
          {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />} Сохранить
        </button>
        <button className="btn" disabled={sending} onClick={sendNow} title="Отправить бэкап прямо сейчас">
          {sending ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />} Отправить сейчас
        </button>
      </div>
    </div>
  );
}
