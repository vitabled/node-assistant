import { useEffect, useState } from "react";
import { Eye, EyeOff, X } from "lucide-react";
import { useStatWidgets, hiddenCount } from "./statWidgetsStore";

type Instance = { id: string; name: string; kind: string };
type CheckerNode = { stableId: string; name: string };

/** Пикер скрытых серверов (Волна 6, План B Ф3).
 *
 *  Две секции, потому что и хранилище двухосевое: ноды Remnawave ключуются на
 *  `node_uuid`, узлы мониторинга — на `stableId` ВНУТРИ конкретного чекера.
 *
 *  Список нод берётся из уже загруженного на странице `nameMap` — новых
 *  запросов не добавляем. Узлы мониторинга подгружаются лениво, только когда
 *  пользователь открыл пикер и выбрал инстанс.
 */
export function HiddenServers({ nameMap, instances, onClose }: {
  nameMap: Record<string, string>;
  instances: Instance[];
  onClose: () => void;
}) {
  const { hidden, hideNode, showNode, hideCheckerNode, showCheckerNode } = useStatWidgets();
  // `server-monitor` тут отсутствует намеренно: у него свой namespace (row-id
  // SQLite) и своё подавление на бэкенде — см. Ф4.
  const checkers = instances.filter(i => i.id !== "server-monitor");
  const [cid, setCid] = useState(checkers[0]?.id ?? "local");
  const [nodes, setNodes] = useState<CheckerNode[] | null>(null);

  useEffect(() => {
    let dead = false;
    setNodes(null);
    fetch(`/api/checker/statuspage?checker_id=${encodeURIComponent(cid)}&ticks=1`)
      .then(r => (r.ok ? r.json() : { nodes: [] }))
      .then(d => { if (!dead) setNodes(d.nodes ?? []); })
      .catch(() => { if (!dead) setNodes([]); });
    return () => { dead = true; };
  }, [cid]);

  const hiddenHere = hidden.checker[cid] ?? {};
  // Записи, которых больше нет в выдаче: чекер мог перегенерировать stableId,
  // а нода — быть удалена. Без этой группы такой ключ нельзя было бы убрать.
  const missingNodes = Object.keys(hidden.nodes).filter(u => !(u in nameMap));
  const missingChecker = Object.keys(hiddenHere).filter(s => !(nodes ?? []).some(n => n.stableId === s));

  const Row = ({ label, isHidden, onToggle }: { label: string; isHidden: boolean; onToggle: () => void }) => (
    <button className="navitem" style={{ textAlign: "left", width: "100%" }} onClick={onToggle}>
      {isHidden ? <EyeOff size={14} style={{ flex: "none", color: "var(--t-low)" }} />
                : <Eye size={14} style={{ flex: "none", color: "var(--accent)" }} />}
      <span className="trunc" style={{ opacity: isHidden ? 0.55 : 1 }}>{label}</span>
    </button>
  );

  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal" style={{ maxWidth: 520 }} onClick={e => e.stopPropagation()}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "14px 16px", borderBottom: "1px solid var(--line-soft)" }}>
          <EyeOff size={16} style={{ color: "var(--accent)" }} />
          <span style={{ fontSize: 14, fontWeight: 600, color: "var(--t-hi)", flex: 1 }}>Серверы в статистике</span>
          <button className="iconbtn" onClick={onClose}><X size={16} /></button>
        </div>

        <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 18, maxHeight: "60vh", overflowY: "auto" }}>
          <section style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <span className="micro">Ноды Remnawave</span>
            {Object.keys(nameMap).length === 0 && (
              <p style={{ fontSize: 12, color: "var(--t-low)" }}>Нет данных о нодах за последние 30 дней.</p>
            )}
            {Object.entries(nameMap).map(([uuid, name]) => (
              <Row key={uuid} label={name} isHidden={uuid in hidden.nodes}
                onToggle={() => (uuid in hidden.nodes ? showNode(uuid) : hideNode(uuid, name))} />
            ))}
            {missingNodes.length > 0 && (
              <>
                <span className="micro" style={{ marginTop: 8 }}>Не найдено в текущих данных</span>
                {missingNodes.map(u => (
                  <Row key={u} label={hidden.nodes[u] || u.slice(0, 8)} isHidden
                    onToggle={() => showNode(u)} />
                ))}
              </>
            )}
          </section>

          <section style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <span className="micro">Мониторинг</span>
            <select className="selectbox" value={cid} onChange={e => setCid(e.target.value)} style={{ marginBottom: 4 }}>
              {checkers.map(i => <option key={i.id} value={i.id}>{i.name}</option>)}
            </select>
            {nodes === null && <p style={{ fontSize: 12, color: "var(--t-low)" }}>Загрузка…</p>}
            {nodes?.length === 0 && <p style={{ fontSize: 12, color: "var(--t-low)" }}>У этого инстанса нет узлов.</p>}
            {(nodes ?? []).map(n => (
              <Row key={n.stableId} label={n.name} isHidden={n.stableId in hiddenHere}
                onToggle={() => (n.stableId in hiddenHere
                  ? showCheckerNode(cid, n.stableId)
                  : hideCheckerNode(cid, n.stableId, n.name))} />
            ))}
            {missingChecker.length > 0 && (
              <>
                <span className="micro" style={{ marginTop: 8 }}>Не найдено в текущих данных</span>
                {missingChecker.map(s => (
                  <Row key={s} label={hiddenHere[s] || s} isHidden onToggle={() => showCheckerNode(cid, s)} />
                ))}
              </>
            )}
            <p style={{ fontSize: 11, color: "var(--t-low)", marginTop: 6 }}>
              «Доступность серверов» на Дэшборде скрывается отдельно — у неё свой список серверов.
            </p>
          </section>

          <p style={{ fontSize: 11, color: "var(--t-low)" }}>
            «Топ пользователей» считается по пользователям и не фильтруется по серверам.
          </p>
        </div>

        <div style={{ padding: "12px 16px", borderTop: "1px solid var(--line-soft)", display: "flex", alignItems: "center" }}>
          <span style={{ fontSize: 12, color: "var(--t-low)", flex: 1 }}>Скрыто: {hiddenCount(hidden)}</span>
          <button className="btn btn-sm" onClick={onClose}>Закрыть</button>
        </div>
      </div>
    </div>
  );
}
