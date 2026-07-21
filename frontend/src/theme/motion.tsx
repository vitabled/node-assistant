// Reusable motion primitives (Wave-5 Plan B). All skin-agnostic; the neon skin
// only adds glow on top. Every entrance/loop is gated by useMotionEnabled(),
// which folds in BOTH the device toggle (ni_motion) and the OS
// prefers-reduced-motion preference — so nothing animates when the user opts out.
import { useEffect, useRef, useState, type ReactNode, type CSSProperties } from "react";
import { motion, useReducedMotion, type Variants } from "motion/react";
import { loadMotion } from "./tweaks";

export function useMotionEnabled(): boolean {
  const reduced = useReducedMotion();
  const [on] = useState(() => loadMotion());
  return on && !reduced;
}

// Tab-transition variant for <AnimatePresence mode="wait">.
export const tabFade: Variants = {
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0, transition: { duration: 0.16, ease: "easeOut" } },
  exit:    { opacity: 0, y: -6, transition: { duration: 0.12, ease: "easeIn" } },
};

const containerV: Variants = { animate: { transition: { staggerChildren: 0.04 } } };
const itemV: Variants = {
  initial: { opacity: 0, y: 6 },
  animate: { opacity: 1, y: 0, transition: { duration: 0.18, ease: "easeOut" } },
};

interface BoxProps { children: ReactNode; className?: string; style?: CSSProperties }

export function Stagger({ children, className, style }: BoxProps) {
  const on = useMotionEnabled();
  if (!on) return <div className={className} style={style}>{children}</div>;
  return (
    <motion.div className={className} style={style} variants={containerV} initial="initial" animate="animate">
      {children}
    </motion.div>
  );
}

export function StaggerItem({ children, className, style }: BoxProps) {
  const on = useMotionEnabled();
  if (!on) return <div className={className} style={style}>{children}</div>;
  return <motion.div className={className} style={style} variants={itemV}>{children}</motion.div>;
}

// Tween a number toward `value`; snaps instantly under reduced-motion / toggle-off.
export function AnimatedNumber(
  { value, decimals = 0, className, style }:
  { value: number; decimals?: number; className?: string; style?: CSSProperties },
) {
  const on = useMotionEnabled();
  const [display, setDisplay] = useState(value);
  const fromRef = useRef(value);
  useEffect(() => {
    if (!on) { setDisplay(value); fromRef.current = value; return; }
    const from = fromRef.current, to = value;
    if (from === to) return;
    const dur = 400;
    let raf = 0, start = 0;
    const tick = (t: number) => {
      if (!start) start = t;
      const p = Math.min(1, (t - start) / dur);
      const eased = 1 - Math.pow(1 - p, 3);
      setDisplay(from + (to - from) * eased);
      if (p < 1) raf = requestAnimationFrame(tick);
      else fromRef.current = to;
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value, on]);
  return <span className={className} style={style}>{display.toFixed(decimals)}</span>;
}

// Shimmer placeholder (CSS in index.css: .ni-skeleton, static under reduced-motion).
export function Skeleton({ className, style }: { className?: string; style?: CSSProperties }) {
  return <div className={`ni-skeleton ${className ?? ""}`} style={style} />;
}
