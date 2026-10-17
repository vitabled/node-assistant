import { loadServers, saveServers, upsertServer, removeServer, normalizeUrl, autoOpenTarget, AUTO_KEY } from "./servers.js";

const $ = id => document.getElementById(id);
const store = window.localStorage;

function snack(text) {
  const el = $("snack");
  el.textContent = text; el.hidden = false;
  clearTimeout(snack.t); snack.t = setTimeout(() => { el.hidden = true; }, 3500);
}

// Проверка, что по адресу живой NA: публичная ручка /api/health (без данных аккаунта).
async function probe(origin) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), 8000);
  try {
    // no-cors: NA не обязан пускать origin приложения (https://localhost) в CORS —
    // нам достаточно, что хост отвечает; ответ остаётся непрозрачным.
    await fetch(`${origin}/api/health`, { signal: ctl.signal, cache: "no-store", mode: "no-cors" });
    return true;
  } catch { return false; } finally { clearTimeout(t); }
}

function open(origin) {
  saveServers(store, upsertServer(loadServers(store), origin));
  // Уходим на origin NA: дальше работает его собственный веб-интерфейс (M3, все разделы),
  // сессия хранится в WebView на его origin. «Назад» на Android возвращает сюда.
  window.location.href = `${origin}/`;
}

function render() {
  const list = loadServers(store);
  const ul = $("list");
  ul.replaceChildren(...list.map(s => {
    const li = document.createElement("li");
    li.className = "list-item";
    const main = document.createElement("button");
    main.className = "list-main";
    main.innerHTML = `<span class="avatar" aria-hidden="true"></span><span class="texts"><span class="body-large"></span><span class="body-medium on-surface-variant"></span></span>`;
    main.querySelector(".avatar").textContent = (s.name[0] || "N").toUpperCase();
    main.querySelector(".body-large").textContent = s.name;
    main.querySelector(".body-medium").textContent = s.url;
    main.onclick = () => open(s.url);
    const del = document.createElement("button");
    del.className = "icon-btn"; del.setAttribute("aria-label", `Удалить ${s.name}`); del.textContent = "✕";
    del.onclick = () => { saveServers(store, removeServer(loadServers(store), s.url)); render(); snack("Сервер удалён"); };
    li.append(main, del);
    return li;
  }));
  $("empty").hidden = list.length > 0;
  $("auto").checked = store.getItem(AUTO_KEY) === "1";
}

$("auto").onchange = e => store.setItem(AUTO_KEY, e.target.checked ? "1" : "0");
$("fab").onclick = () => { $("form").reset(); $("err").hidden = true; $("dlg").showModal(); $("url").focus(); };
$("cancel").onclick = () => $("dlg").close();
$("form").onsubmit = async e => {
  e.preventDefault();
  const origin = normalizeUrl($("url").value);
  const err = $("err");
  if (!origin) { err.textContent = "Некорректный адрес (нужен http(s)://хост)"; err.hidden = false; return; }
  $("save").disabled = true; err.hidden = true;
  const ok = await probe(origin);
  $("save").disabled = false;
  if (!ok) { err.textContent = "Сервер не отвечает. Проверьте адрес и сеть."; err.hidden = false; return; }
  saveServers(store, upsertServer(loadServers(store), origin, $("name").value));
  $("dlg").close();
  open(origin);
};

// Автооткрытие — один раз за запуск: «Назад» из NA возвращает к списку серверов.
const target = sessionStorage.getItem("na_auto_done") ? null : autoOpenTarget(store, window.location.search);
sessionStorage.setItem("na_auto_done", "1");
if (target) window.location.href = `${target}/`;
else render();
