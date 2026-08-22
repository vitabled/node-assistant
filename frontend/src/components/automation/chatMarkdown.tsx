// Ответ ассистента → React-узлы: markdown + ОГРАНИЧЕННЫЙ HTML.
//
// Текст приходит от модели, то есть это НЕДОВЕРЕННЫЙ ввод: в нём может быть и
// `<script>`, и `<img onerror=…>`, и `href="javascript:…"`. Поэтому конвейер
// такой:
//
//   marked (gfm) → строка HTML
//     → DOMPurify c allowlist → DocumentFragment (уже без опасных узлов)
//       → обход фрагмента → React-элементы
//
// Последний шаг — не украшательство: он позволяет обойтись БЕЗ
// `dangerouslySetInnerHTML` вообще. React строит дерево сам, а список тегов
// проверяется второй раз, уже на нашей стороне (defence in depth): даже если
// когда-нибудь DOMPurify пропустит незнакомый узел, до DOM он не доедет — тег
// не из белого списка рисуется как его собственные дети, то есть текстом.
//
// Обсидиановый рендер (`library/markdown.ts`) сюда не подходит: он тянет
// wiki-ссылки и авторизованную подгрузку медиа, которых в чате нет.
import { createElement, type ReactNode } from "react";
import { marked } from "marked";
import DOMPurify from "dompurify";

/** Что вообще может оказаться в ответе. Всё остальное DOMPurify выкидывает. */
const ALLOWED_TAGS = [
  "p", "br", "hr", "span", "div",
  "strong", "b", "em", "i", "del", "s",
  "code", "pre", "blockquote",
  "a", "ul", "ol", "li",
  "h1", "h2", "h3", "h4", "h5", "h6",
  "table", "thead", "tbody", "tr", "th", "td",
];

/** `class` намеренно НЕ пропускаем: оформление задаём мы, а не модель. */
const ALLOWED_ATTR = ["href", "title"];

const TAGS = new Set(ALLOWED_TAGS);
/** Теги без детей — React ругается на `children` у void-элементов. */
const VOID = new Set(["br", "hr"]);

// Стиль пузыря не меняется — внутри те же токены палитры, что и везде в панели.
const CLS: Record<string, string> = {
  p: "whitespace-pre-wrap",
  h1: "text-[15px] font-semibold text-[var(--t-hi)]",
  h2: "text-[14px] font-semibold text-[var(--t-hi)]",
  h3: "text-[13px] font-semibold text-[var(--t-hi)]",
  h4: "text-[13px] font-semibold text-[var(--t-mid)]",
  h5: "text-[12px] font-semibold text-[var(--t-mid)]",
  h6: "text-[12px] font-semibold text-[var(--t-low)]",
  ul: "list-disc pl-5 space-y-0.5",
  ol: "list-decimal pl-5 space-y-0.5",
  a: "text-[var(--accent-hi)] underline underline-offset-2 break-words",
  // Код-блок — тёмная плашка; inline-код — акцентом, как в подсказке про команды.
  pre: "p-2.5 rounded-lg bg-[var(--bg3)] border border-[var(--line-soft)] overflow-x-auto",
  blockquote: "pl-2.5 border-l-2 border-[var(--line)] text-[var(--t-low)]",
  hr: "border-0 border-t border-[var(--line-soft)]",
  table: "w-full border-collapse text-[12px]",
  th: "border border-[var(--line-soft)] bg-[var(--bg3)] text-[var(--t-hi)] font-semibold px-2 py-1 text-left",
  td: "border border-[var(--line-soft)] px-2 py-1 align-top",
};
const CODE_INLINE = "font-mono text-[12px] px-1 py-px rounded bg-[var(--bg3)] text-[var(--accent-hi)]";
const CODE_BLOCK = "font-mono text-[12px] text-[var(--t-low)] whitespace-pre";

/** Только сетевые схемы. `javascript:` и `data:` DOMPurify снимает сам, но
 *  ссылка без href всё равно не должна выглядеть кликабельной. */
const SAFE_HREF = /^(https?:\/\/|mailto:)/i;

function attrsFor(el: Element, tag: string, inPre: boolean): Record<string, unknown> {
  const props: Record<string, unknown> = {};
  const cls = tag === "code" ? (inPre ? CODE_BLOCK : CODE_INLINE) : CLS[tag];
  if (cls) props.className = cls;
  const title = el.getAttribute("title");
  if (title) props.title = title;
  if (tag === "a") {
    const href = el.getAttribute("href") || "";
    if (!SAFE_HREF.test(href)) return props;      // остаётся <a> без href — просто текст
    props.href = href;
    // Уводить SPA по внешней ссылке нельзя, а `noreferrer` заодно закрывает
    // доступ к `window.opener`.
    props.target = "_blank";
    props.rel = "noreferrer noopener";
  }
  return props;
}

function toReact(node: Node, key: number, inPre: boolean): ReactNode {
  if (node.nodeType === 3) return node.nodeValue;                       // текст
  if (node.nodeType !== 1) return null;                                 // коммент и пр.

  const el = node as Element;
  const tag = el.tagName.toLowerCase();
  const kids = () => Array.from(el.childNodes)
    .map((c, i) => toReact(c, i, inPre || tag === "pre"))
    .filter(c => c !== null && c !== "");

  // Тег не из белого списка: показываем его содержимое, сам узел выбрасываем.
  if (!TAGS.has(tag)) return createElement("span", { key }, ...kids());
  if (VOID.has(tag)) return createElement(tag, { key, ...attrsFor(el, tag, inPre) });
  return createElement(tag, { key, ...attrsFor(el, tag, inPre) }, ...kids());
}

/** Markdown + безопасный HTML → React-узлы. Пустой текст → пустой массив. */
export function renderRich(text: string): ReactNode[] {
  const src = text || "";
  if (!src.trim()) return [];
  // `async: false` — в чате нет расширений marked, парсер синхронный; тип
  // объявлен как `string | Promise<string>`, поэтому сужаем явно.
  const html = marked.parse(src, { gfm: true, breaks: true, async: false }) as string;
  const frag = DOMPurify.sanitize(html, {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
    // Ссылки-схемы: всё, что не http(s)/mailto, теряет href ещё здесь.
    ALLOWED_URI_REGEXP: SAFE_HREF,
    RETURN_DOM_FRAGMENT: true,
  }) as unknown as DocumentFragment;
  return Array.from(frag.childNodes)
    .map((n, i) => toReact(n, i, false))
    .filter(n => n !== null && n !== "");
}

/** Пузырь ответа ассистента. Стиль (фон, рамка, размер) — прежний. */
export function RichText({ text, className = "" }: { text: string; className?: string }) {
  return <div className={`ni-rich flex flex-col gap-1.5 ${className}`}>{renderRich(text)}</div>;
}
