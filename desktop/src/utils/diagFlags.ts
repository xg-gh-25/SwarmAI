/**
 * Runtime diagnostic flags — opt-in instrumentation that works in PRODUCTION.
 *
 * Why this exists: instrumentation gated only on `import.meta.env.DEV` is
 * tree-shaken out of the production Tauri .app build (DEV === false), so it can
 * NEVER fire where the user actually hits the bug (run_3451bbd1: the OT01
 * store-vs-render diag was dead in prod for exactly this reason). These helpers
 * are TRUE in DEV automatically AND can be flipped on in a production build via
 * a localStorage flag — no rebuild needed to toggle:
 *
 *     localStorage.setItem('SWARM_OT01_DIAG', '1')   // enable (then reproduce)
 *     localStorage.removeItem('SWARM_OT01_DIAG')      // disable
 *
 * localStorage access is try-guarded: a Tauri/webview context that throws on
 * storage access must never break a render or a stream handler.
 *
 * @exports isOt01DiagEnabled — store-vs-render content-loss diagnostic gate
 */

// Cached once at module load: the flag cannot change mid-session without a page
// reload (a devtools localStorage.setItem requires reload to re-enter this
// module), so reading localStorage once is correct AND provably zero-cost on the
// per-render / per-sync hot paths that call isOt01DiagEnabled() (Gate-2 LOW nit,
// run_3451bbd1). import.meta.env.DEV is a compile-time constant (true in DEV;
// folded to false in the prod build, leaving the localStorage read live).
let _ot01Enabled: boolean | null = null;

/** True when the OT01 content-loss diagnostic should log (DEV, or prod opt-in). */
export function isOt01DiagEnabled(): boolean {
  if (_ot01Enabled === null) {
    if (import.meta.env.DEV) {
      _ot01Enabled = true;
    } else {
      try {
        _ot01Enabled = localStorage.getItem('SWARM_OT01_DIAG') === '1';
      } catch {
        _ot01Enabled = false;
      }
    }
  }
  return _ot01Enabled;
}
