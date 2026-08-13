import { useEffect, useState } from "react";
import { Loader2, Save, ShieldAlert } from "lucide-react";
import { toast } from "./infra/Toast";

/**
 * «Fail2Ban list» (Wave-5 PR-2): список IP/CIDR, который backend применяет
 * на сервере при любом деплое (banip + персистентность, удалённое — unban).
 */
export function F2bList() {
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    fetch("/api/f2b-list").then(r => r.json())
      .then(d => setText((d.entries || []).join("\n")))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const save = async () => {
    setSaving(true); setErr("");
    try {
      const entries = text.split("\n").map(s => s.trim()).filter(Boolean);
      const res = await fetch("/api/f2b-list", {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ entries }),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) { setErr(typeof d.detail === "string" ? d.detail : `HTTP ${res.status}`); return; }
      setText((d.entries || []).join("\n"));
      setDirty(false);
      toast(`Fail2Ban list сохранён (${d.count})`, "success");
    } finally { setSaving(false); }
  };

  return (
    <div className="card card-p" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div className="flex items-center gap-2">
        <ShieldAlert size={14} style={{ color: "var(--warn)" }} />
        <span className="micro">Fail2Ban list</span>
        {dirty && <span className="chip warn" style={{ fontSize: 10 }}>изменён</span>}
      </div>
      <p className="hint" style={{ marginTop: 0 }}>
        IP/CIDR по строке — автоматически банятся при любом деплое
        (нода/панель/SSL). Убрали из списка — при следующем деплое разбанится.
      </p>
      {loading ? (
        <Loader2 size={14} className="spin" style={{ color: "var(--t-faint)" }} />
      ) : (
        <textarea className="input font-mono text-xs" rows={6} value={text}
          data-testid="f2b-textarea"
          onChange={e => { setText(e.target.value); setDirty(true); }}
          placeholder={"203.0.113.10\n198.51.100.0/24"} spellCheck={false} />
      )}
      {err && <p className="errmsg">{err}</p>}
      <button className="btn btn-soft" style={{ alignSelf: "flex-start" }} onClick={save}
        disabled={saving || loading || !dirty}>
        {saving ? <Loader2 size={13} className="spin" /> : <Save size={13} />} Сохранить список
      </button>
    </div>
  );
}
