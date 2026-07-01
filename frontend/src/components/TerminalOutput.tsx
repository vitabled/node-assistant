import { useEffect, useRef } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";

interface Props {
  lines: string[];
}

export function TerminalOutput({ lines }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const termRef      = useRef<Terminal | null>(null);
  const fitRef       = useRef<FitAddon | null>(null);
  const lastIndexRef = useRef(0);

  // Mount terminal once
  useEffect(() => {
    if (!containerRef.current) return;

    const term = new Terminal({
      theme: {
        background:         "#0d1117",
        foreground:         "#c9d1d9",
        cursor:             "#58a6ff",
        selectionBackground:"#264f78",
        black:              "#0d1117",
        brightBlack:        "#6e7681",
        red:                "#ff7b72",
        brightRed:          "#ffa198",
        green:              "#3fb950",
        brightGreen:        "#56d364",
        yellow:             "#d29922",
        brightYellow:       "#e3b341",
        blue:               "#58a6ff",
        brightBlue:         "#79c0ff",
        cyan:               "#39c5cf",
        brightCyan:         "#56d4dd",
        white:              "#b1bac4",
        brightWhite:        "#f0f6fc",
      },
      fontSize:        13,
      fontFamily:      "JetBrains Mono, Fira Code, Consolas, monospace",
      lineHeight:      1.4,
      letterSpacing:   0.3,
      convertEol:      true,
      scrollback:      5000,
      cursorBlink:     true,
      cursorStyle:     "bar",
      allowTransparency: true,
    });

    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(containerRef.current);

    // fit() needs the element to be visible/sized; defer one tick
    requestAnimationFrame(() => fit.fit());

    termRef.current = term;
    fitRef.current  = fit;

    const obs = new ResizeObserver(() => fitRef.current?.fit());
    obs.observe(containerRef.current);

    return () => {
      obs.disconnect();
      term.dispose();
      termRef.current  = null;
      fitRef.current   = null;
      lastIndexRef.current = 0;
    };
  }, []);

  // Write incremental lines; detect reset when array shrinks to 0
  useEffect(() => {
    const term = termRef.current;
    if (!term) return;

    // Reset — new deploy started
    if (lines.length === 0 && lastIndexRef.current > 0) {
      term.reset();
      lastIndexRef.current = 0;
      return;
    }

    const newLines = lines.slice(lastIndexRef.current);
    if (newLines.length === 0) return;

    newLines.forEach((l) => term.writeln(l));
    lastIndexRef.current = lines.length;
    term.scrollToBottom();
  }, [lines]);

  return (
    <div
      ref={containerRef}
      className="h-full w-full rounded-lg overflow-hidden"
      style={{ background: "#0d1117" }}
    />
  );
}
