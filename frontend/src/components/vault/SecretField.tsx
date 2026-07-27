// Controlled secret input (Wave-9 Plan A Ф6). One component for both roles:
// entering a secret in EntryModal and displaying a revealed one in Vault.
//  • eye toggle → password ↔ text
//  • «Копировать» → navigator.clipboard
//  • `saved` → placeholder telling the user that blank keeps the stored secret
import { useState, type CSSProperties } from "react";
import { Eye, EyeOff, Copy, Check } from "lucide-react";
import { toast } from "../infra/Toast";
import type { VaultFieldKind } from "./api";

// A <textarea> has no type="password", so masking uses the standard
// `-webkit-text-security` trick. It is not in csstype's CSSProperties, hence the
// cast; unsupported engines simply show the text (the eye still works).
const MASK_STYLE = { WebkitTextSecurity: "disc" } as unknown as CSSProperties;

export function SecretField({
  label, value, onChange, kind = "password", saved = false,
  placeholder, hint, readOnly = false, defaultVisible = false, rows = 6,
}: {
  label: string;
  value: string;
  onChange?: (v: string) => void;
  kind?: VaultFieldKind;
  /** Existing entry: the stored secret survives an empty submit. */
  saved?: boolean;
  placeholder?: string;
  hint?: string;
  readOnly?: boolean;
  defaultVisible?: boolean;
  rows?: number;
}) {
  const [visible, setVisible] = useState(defaultVisible);
  const [copied, setCopied] = useState(false);

  // A plain-text schema field (a login name) is not a secret — no masking, but
  // copying still helps.
  const maskable = kind !== "text";
  const hidden = maskable && !visible;

  const ph = placeholder ?? (saved ? "сохранено — оставьте пустым, чтобы не менять" : "");

  const copy = async () => {
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      toast("Не удалось скопировать — буфер обмена недоступен", "error");
    }
  };

  return (
    <div className="flex flex-col gap-1">
      <label className="label">{label}</label>
      <div className="flex items-start gap-2">
        {kind === "textarea" ? (
          <textarea
            value={value}
            onChange={e => onChange?.(e.target.value)}
            readOnly={readOnly}
            rows={rows}
            placeholder={ph}
            spellCheck={false}
            autoComplete="off"
            style={hidden ? MASK_STYLE : undefined}
            className="input flex-1 font-mono text-xs"
          />
        ) : (
          <input
            type={hidden ? "password" : "text"}
            value={value}
            onChange={e => onChange?.(e.target.value)}
            readOnly={readOnly}
            placeholder={ph}
            spellCheck={false}
            autoComplete="off"
            className="input flex-1 font-mono text-xs"
          />
        )}
        {maskable && (
          <button type="button" onClick={() => setVisible(v => !v)}
            title={hidden ? "Показать" : "Скрыть"}
            className="p-2 rounded-md border border-[var(--line)] text-[var(--t-mid)] hover:bg-[var(--bg3)] shrink-0">
            {hidden ? <Eye size={14} /> : <EyeOff size={14} />}
          </button>
        )}
        <button type="button" onClick={copy} disabled={!value} title="Копировать"
          className="p-2 rounded-md border border-[var(--line)] text-[var(--t-mid)] hover:bg-[var(--bg3)] disabled:opacity-40 shrink-0">
          {copied ? <Check size={14} className="text-[var(--ok)]" /> : <Copy size={14} />}
        </button>
      </div>
      {hint && <p className="hint">{hint}</p>}
    </div>
  );
}
