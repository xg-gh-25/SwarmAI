/** Generated built-in App bindings. DO NOT EDIT.
 * Regenerate with `python backend/scripts/generate_app_registry.py`.
 */
export const BUILTIN_APP_BINDING_DIGEST = "00a5415e8e957354cbb993fba47c24556fadc941f5706a90679cedc2eb06cab3" as const;

export const BUILTIN_APP_SURFACE_IMPORTS = {
  "pollinate/surface:gallery": () => import("../components/layout/PollinateOverlay"),
} as const;

export const BUILTIN_APP_CLIENT_IMPORTS = {
  "pollinate/host-binding:frontend-client": () => import("../services/pollinate"),
} as const;

export type BuiltinAppSurface = keyof typeof BUILTIN_APP_SURFACE_IMPORTS;
export type BuiltinAppClient = keyof typeof BUILTIN_APP_CLIENT_IMPORTS;
