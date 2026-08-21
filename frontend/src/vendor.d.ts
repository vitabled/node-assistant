// Bundled world topojson (world-atlas package) — imported as a plain object and
// handed to topojson-client. Typed as `any` to avoid a multi-MB inferred literal.
declare module "world-atlas/countries-110m.json" {
  const topology: any;
  export default topology;
}

// TypeScript's noUncheckedSideEffectImports needs an ambient declaration for
// stylesheet side effects (local CSS and package-provided CSS alike).
declare module "*.css";
