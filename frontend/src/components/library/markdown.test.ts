// Wiki syntax → HTML. The rewrite runs BEFORE marked, so everything asserted
// here is what marked (and then DOMPurify) receives.
import { describe, it, expect, beforeAll } from "vitest";
import { expandWiki, embedIds, cacheMedia, type NoteRef } from "./markdown";

const NOTES: NoteRef[] = [
  { id: "n1", name: "Заметка" },
  { id: "n2", name: "Инфра" },
];

// Priming the cache is what the editor does after an upload — it keeps these
// tests off the network (`expandWiki` itself never fetches).
beforeAll(() => {
  cacheMedia({ id: "m1", name: "скрин.png", mime: "image/png", size: 2048, inline: true, created_at: 0 });
  cacheMedia({ id: "m2", name: "спека.pdf", mime: "application/pdf", size: 1024, inline: false, created_at: 0 });
});

describe("expandWiki: links", () => {
  it("resolves a link to an existing note", () => {
    const out = expandWiki("см. [[Заметка]] далее", NOTES);
    expect(out).toContain('data-note="n1"');
    expect(out).not.toContain("wikilink-missing");
    expect(out).toContain(">Заметка</a>");
  });

  it("matches the name case-insensitively", () => {
    expect(expandWiki("[[заМЕТка]]", NOTES)).toContain('data-note="n1"');
  });

  it("marks a link with no target as missing, keeping the name for creation", () => {
    const out = expandWiki("[[Ещё нет]]", NOTES);
    expect(out).toContain("wikilink-missing");
    expect(out).toContain('data-note=""');
    expect(out).toContain('data-name="Ещё нет"');
  });

  it("uses the alias as the label and keeps the anchor visible", () => {
    expect(expandWiki("[[Заметка|иначе]]", NOTES)).toContain(">иначе</a>");
    expect(expandWiki("[[Заметка#Раздел]]", NOTES)).toContain(">Заметка › Раздел</a>");
    expect(expandWiki("[[Заметка#Раздел|коротко]]", NOTES)).toContain(">коротко</a>");
  });

  it("escapes the label — a note name is user input on the way into HTML", () => {
    const out = expandWiki("[[Заметка|<img src=x onerror=alert(1)>]]", NOTES);
    expect(out).toContain("&lt;img");
    expect(out).not.toContain("<img src=x");
  });
});

describe("expandWiki: media embeds", () => {
  it("renders a cached raster image WITHOUT src (the viewer fetches it with auth)", () => {
    const out = expandWiki("![[m1]]", NOTES);
    expect(out).toContain('data-media="m1"');
    expect(out).toContain("<img");
    expect(out).not.toContain("src=");
  });

  it("renders a non-inline attachment as a download chip", () => {
    const out = expandWiki("![[m2]]", NOTES);
    expect(out).toContain('class="md-file"');
    expect(out).toContain("спека.pdf");
  });

  it("takes the caption over the stored name", () => {
    expect(expandWiki("![[m1|схема сети]]", NOTES)).toContain('alt="схема сети"');
  });
});

describe("expandWiki: code is left alone", () => {
  const fenced = "```\n[[Заметка]] ![[m1]]\n```";
  const inline = "пиши `[[Заметка]]` вот так";

  it("does not linkify inside a fenced block", () => {
    expect(expandWiki(fenced, NOTES)).toBe(fenced);
  });

  it("does not linkify inside an inline code span", () => {
    expect(expandWiki(inline, NOTES)).toBe(inline);
  });

  it("still linkifies around the code", () => {
    const out = expandWiki("до [[Заметка]]\n" + fenced + "\nпосле [[Инфра]]", NOTES);
    expect(out).toContain('data-note="n1"');
    expect(out).toContain('data-note="n2"');
    expect(out).toContain("[[Заметка]] ![[m1]]");   // the fenced copy survives verbatim
  });
});

describe("embedIds", () => {
  it("collects embedded ids and ignores code and plain links", () => {
    expect(embedIds("![[a]] [[Заметка]] ![[b|подпись]] `![[c]]`")).toEqual(["a", "b"]);
  });

  it("returns nothing for a note without embeds", () => {
    expect(embedIds("просто текст [[Заметка]]")).toEqual([]);
  });
});
