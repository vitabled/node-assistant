// «Cloudflare → Домены» — свои регистрации + покупка нового домена.
//
// Purchase spends real money, so the modal is deliberately unhurried: the price
// comes from the server's own check (never a local guess), `expected_price` is
// echoed back so the backend can refuse on drift, auto-renew starts OFF, and the
// button stays disabled until the user ticks an explicit acknowledgement.
import { useCallback, useEffect, useRef, useState } from "react";
import { Globe, RefreshCw, Search, ShoppingCart } from "lucide-react";
import { Page, PageHeader, Modal, Field } from "../infra/ui";
import { toast } from "../infra/Toast";
import { useCfReady, CfNotConnected } from "./gate";
import {
  listDomains, checkDomains, searchDomains, patchDomain, registerDomain,
  getConfig, fmtMoney, fmtDay, messageOf, type CfDomain, type CfCandidate,
} from "./api";

interface Contact {
  first_name: string; last_name: string; organization: string;
  email: string; phone: string;
  country: string; city: string; postal_code: string; state: string; address: string;
}

const EMPTY_CONTACT: Contact = {
  first_name: "", last_name: "", organization: "", email: "", phone: "",
  country: "", city: "", postal_code: "", state: "", address: "",
};

/** Cloudflare's contact shape: name/organization at the top, the rest in postal_info. */
const toCfContact = (c: Contact) => ({
  registrant: {
    first_name: c.first_name, last_name: c.last_name,
    organization: c.organization || undefined,
    email: c.email, phone: c.phone,
    postal_info: {
      city: c.city, country_code: c.country.toUpperCase(),
      postal_code: c.postal_code, state: c.state, street: c.address,
    },
  },
});

function BuyModal({ cand, contact0, onClose, onDone }: {
  cand: CfCandidate;
  contact0: Contact;
  onClose: () => void;
  onDone: () => void;
}) {
  const [years, setYears] = useState(Math.max(1, cand.period_years || 1));
  const [privacy, setPrivacy] = useState(true);
  const [autoRenew, setAutoRenew] = useState(false);
  const [ack, setAck] = useState(false);
  const [c, setC] = useState<Contact>(contact0);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [state, setState] = useState("");

  const contactOk = c.first_name && c.last_name && c.email && c.phone
    && c.country.trim().length === 2 && c.city && c.postal_code && c.address;
  const canBuy = ack && !!cand.price && !!contactOk && !busy;

  const buy = async () => {
    if (!cand.price) return;
    setBusy(true); setErr("");
    try {
      const r = await registerDomain({
        domain_name: cand.name,
        years,
        privacy_mode: privacy ? "redaction" : "off",
        auto_renew: autoRenew,
        contacts: toCfContact(c),
        confirm: true,
        // From the server's check for THIS name — the backend re-checks and 409s
        // if the registry price moved.
        expected_price: cand.price,
        expected_currency: cand.currency,
      });
      setState(r.state);
      if (r.state === "succeeded") {
        toast(`Домен ${r.domain} зарегистрирован`, "success");
        onDone();
      } else {
        toast(`Заявка принята, статус: ${r.state}`, "info");
      }
    } catch (e) {
      setErr(messageOf(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      title={`Покупка ${cand.name}`}
      onClose={onClose}
      wide
      footer={<>
        <button className="btn" onClick={onClose}>Отмена</button>
        <button className="btn btn-primary" disabled={!canBuy} onClick={buy}>
          <ShoppingCart size={14} /> {busy ? "Оформление…" : `Купить за ${fmtMoney(cand.price, cand.currency)}`}
        </button>
      </>}
    >
      <div className="rounded-lg p-3"
        style={{ background: "var(--panel)", border: "1px solid var(--line)" }}>
        <p style={{ fontSize: 24, fontWeight: 700, color: "var(--t-hi)", lineHeight: 1.2 }}>
          {fmtMoney(cand.price, cand.currency)}
        </p>
        <p className="micro" style={{ color: "var(--t-low)" }}>
          за {years} {years === 1 ? "год" : "года/лет"} · списание с сохранённого способа оплаты Cloudflare
        </p>
      </div>

      <label className="flex flex-col gap-1">
        <span className="micro">Срок регистрации</span>
        <select className="input" value={years} onChange={e => setYears(Number(e.target.value))}>
          {Array.from({ length: 10 }, (_, i) => i + 1).map(y => (
            <option key={y} value={y}>{y}</option>
          ))}
        </select>
      </label>

      <label className="flex items-center gap-2 text-sm" style={{ color: "var(--t-hi)" }}>
        <input type="checkbox" checked={privacy} onChange={e => setPrivacy(e.target.checked)} />
        Скрывать контактные данные в WHOIS
      </label>

      <label className="flex items-center gap-2 text-sm" style={{ color: "var(--t-hi)" }}>
        <input type="checkbox" checked={autoRenew} onChange={e => setAutoRenew(e.target.checked)} />
        Автопродление
      </label>
      <p className="micro" style={{ color: "var(--t-low)", marginTop: -6 }}>
        Включение разрешает Cloudflare списывать оплату за продление без запроса.
      </p>

      <p className="micro" style={{ marginTop: 4 }}>Контакт регистранта</p>
      <div className="grid gap-2" style={{ gridTemplateColumns: "1fr 1fr" }}>
        <Field label="Имя" value={c.first_name} onChange={v => setC({ ...c, first_name: v })} />
        <Field label="Фамилия" value={c.last_name} onChange={v => setC({ ...c, last_name: v })} />
        <Field label="Email" value={c.email} onChange={v => setC({ ...c, email: v })} />
        <Field label="Телефон" value={c.phone} onChange={v => setC({ ...c, phone: v })}
          placeholder="+31201234567" />
        <Field label="Организация" value={c.organization}
          onChange={v => setC({ ...c, organization: v })} placeholder="необязательно" />
        <Field label="Страна (2 буквы)" value={c.country}
          onChange={v => setC({ ...c, country: v.slice(0, 2).toUpperCase() })} placeholder="NL" />
        <Field label="Город" value={c.city} onChange={v => setC({ ...c, city: v })} />
        <Field label="Индекс" value={c.postal_code} onChange={v => setC({ ...c, postal_code: v })} />
        <Field label="Регион" value={c.state} onChange={v => setC({ ...c, state: v })} />
        <Field label="Улица, дом" value={c.address} onChange={v => setC({ ...c, address: v })} />
      </div>

      <label className="flex items-start gap-2 text-sm" style={{ color: "var(--t-hi)" }}>
        <input type="checkbox" checked={ack} onChange={e => setAck(e.target.checked)}
          style={{ marginTop: 3 }} />
        <span>Понимаю, что будет списание с моего способа оплаты в Cloudflare</span>
      </label>

      {state && state !== "succeeded" && (
        <p className="micro" style={{ color: "var(--t-low)" }}>
          Статус заявки: {state}. Список доменов обновится, когда Cloudflare завершит регистрацию.
        </p>
      )}
      {err && <p className="micro" style={{ color: "var(--err, var(--accent))" }}>{err}</p>}
      <p className="micro" style={{ color: "var(--t-low)" }}>
        Платёжные данные вводятся только в панели Cloudflare — node-assistant их не
        собирает и не хранит.
      </p>
    </Modal>
  );
}

export function CfDomains() {
  const { ready, loading: gate } = useCfReady();
  const [mine, setMine] = useState<CfDomain[]>([]);
  const [q, setQ] = useState("");
  const [cands, setCands] = useState<CfCandidate[]>([]);
  const [buying, setBuying] = useState<CfCandidate | null>(null);
  const [contact, setContact] = useState<Contact>(EMPTY_CONTACT);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const poll = useRef<number | null>(null);

  const load = useCallback((refresh = false) => {
    setBusy(true);
    listDomains(refresh)
      .then(r => { setMine(Array.isArray(r) ? r : []); setErr(""); })
      .catch(e => setErr(messageOf(e)))
      .finally(() => setBusy(false));
  }, []);

  useEffect(() => { if (ready) load(); }, [ready, load]);

  // Prefill the registrant from the saved default contact, if the account has one.
  useEffect(() => {
    if (!ready) return;
    getConfig()
      .then(cfg => {
        const d = cfg.default_contact as Partial<Contact> | undefined;
        if (d && typeof d === "object") setContact({ ...EMPTY_CONTACT, ...d });
      })
      .catch(() => {});
  }, [ready]);

  useEffect(() => () => { if (poll.current) window.clearInterval(poll.current); }, []);

  const find = async () => {
    const raw = q.trim().toLowerCase();
    if (!raw) return;
    setBusy(true); setErr(""); setCands([]);
    try {
      // A full domain gets an exact availability+price check; a bare word goes to
      // the suggestion endpoint.
      const rows = raw.includes(".") ? await checkDomains([raw]) : await searchDomains(raw);
      setCands(rows);
      if (rows.length === 0) setErr("Ничего не найдено");
    } catch (e) {
      setErr(messageOf(e));
    } finally {
      setBusy(false);
    }
  };

  const toggleRenew = async (d: CfDomain) => {
    const name = String(d.name || "");
    if (!name) return;
    try {
      await patchDomain(name, { auto_renew: !d.auto_renew });
      load(true);
    } catch (e) {
      toast(messageOf(e), "error");
    }
  };

  if (gate) return <Page><p className="micro">Загрузка…</p></Page>;
  if (!ready) return <CfNotConnected title="Cloudflare: домены" />;

  return (
    <Page>
      <PageHeader
        icon={<Globe size={18} />}
        title="Cloudflare: домены"
        subtitle="Регистрации аккаунта и покупка нового домена"
        actions={
          <button className="btn" disabled={busy} onClick={() => load(true)}>
            <RefreshCw size={14} /> Обновить
          </button>
        }
      />

      {err && <p className="micro" style={{ color: "var(--err, var(--accent))" }}>{err}</p>}

      <p className="micro" style={{ marginBottom: 6 }}>Мои домены</p>
      {mine.length === 0 ? (
        <p className="micro" style={{ color: "var(--t-low)", marginBottom: 16 }}>
          В Cloudflare Registrar нет доменов на этом аккаунте.
        </p>
      ) : (
        <div className="overflow-x-auto" style={{ marginBottom: 20 }}>
          <table className="tbl">
            <thead>
              <tr><th>Домен</th><th>Действует до</th><th>Автопродление</th><th>WHOIS скрыт</th><th>Статус</th></tr>
            </thead>
            <tbody>
              {mine.map((d, i) => (
                <tr key={String(d.name ?? i)}>
                  <td>{String(d.name ?? "—")}</td>
                  <td>{fmtDay(d.expires_at)}</td>
                  <td>
                    <label className="flex items-center gap-2">
                      <input type="checkbox" checked={!!d.auto_renew} onChange={() => toggleRenew(d)} />
                      <span className="micro">{d.auto_renew ? "вкл" : "выкл"}</span>
                    </label>
                  </td>
                  <td className="micro">{d.privacy ? "да" : "нет"}</td>
                  <td className="micro">{String(d.status ?? "—")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="micro" style={{ marginBottom: 6 }}>Купить домен</p>
      <div className="flex gap-2 mb-3" style={{ maxWidth: 480 }}>
        <input className="input" value={q} onChange={e => setQ(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter") find(); }}
          placeholder="example.com или ключевое слово" />
        <button className="btn" disabled={busy || !q.trim()} onClick={find}>
          <Search size={14} /> Проверить
        </button>
      </div>

      {cands.length > 0 && (
        <div className="overflow-x-auto">
          <table className="tbl">
            <thead><tr><th>Домен</th><th>Доступность</th><th>Цена</th><th>Срок</th><th /></tr></thead>
            <tbody>
              {cands.map(c => (
                <tr key={c.name}>
                  <td>{c.name}</td>
                  <td className="micro" style={{ color: c.available ? "var(--ok, var(--accent))" : "var(--t-low)" }}>
                    {c.available ? "свободен" : "занят"}
                  </td>
                  <td>{fmtMoney(c.price, c.currency)}</td>
                  <td className="micro">{c.period_years || 1} г.</td>
                  <td>
                    <button className="btn" disabled={!c.available || c.price === null}
                      title={c.price === null ? "Cloudflare не вернул цену — покупка недоступна" : ""}
                      onClick={() => setBuying(c)}>
                      Купить
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {buying && (
        <BuyModal
          cand={buying}
          contact0={contact}
          onClose={() => setBuying(null)}
          onDone={() => { setBuying(null); setCands([]); load(true); }}
        />
      )}
    </Page>
  );
}
