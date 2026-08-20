import { AlertTriangle } from "lucide-react";

/** Shown on every BEDOLAGA page when base_url/token aren't configured yet. */
export function NotConfigured() {
  return (
    <div className="py-16 text-center text-[var(--t-faint)]">
      <AlertTriangle size={28} className="inline mb-3 text-[var(--warn,#f59e0b)]" />
      <p className="text-sm font-medium text-[var(--t-mid)]">Подключение к Bedolaga не настроено</p>
      <p className="text-xs mt-1">Откройте раздел «AI Провайдеры» → «Подключение» и укажите webapi URL и токен бота.</p>
    </div>
  );
}
