// Embeds the self-contained Mihomo (Clash.Meta) configurator, vendored under
// public/mihomo/ and served as a static asset. It is a vanilla-JS global-script
// app (own DOM + styles), so we host it in a same-origin iframe rather than
// porting 3.5k lines to React. Same-origin (no sandbox) so its localStorage
// (ui-lang), clipboard copy, config download and file import all work.
// Purely client-side — nothing touches our backend.
export function MihomoEditor() {
  return (
    <div
      className="ni-pagebody"
      style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}
    >
      <iframe
        src="/mihomo/index.html"
        title="Mihomo Configurator"
        style={{
          flex: 1,
          width: "100%",
          border: "none",
          background: "var(--bg)",
        }}
      />
    </div>
  );
}
