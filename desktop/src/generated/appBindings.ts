/** Generated App bindings. DO NOT EDIT.
 * Regenerate with `python backend/scripts/generate_app_registry.py`.
 */
export const BUILTIN_APP_BINDING_DIGEST = "f3c03b8815d2f626aa5ca1ff59cb9a0d46ac22ffa737a303e7475fd3089e2d81" as const;

export const BUILTIN_APP_SURFACE_IMPORTS = {
  "pollinate/surface:gallery": () => import("../components/layout/PollinateOverlay"),
} as const;

export const BUILTIN_APP_CLIENT_IMPORTS = {
  "pollinate/host-binding:frontend-client": () => import("../services/pollinate"),
} as const;

export type BuiltinAppSurface = keyof typeof BUILTIN_APP_SURFACE_IMPORTS;
export type BuiltinAppClient = keyof typeof BUILTIN_APP_CLIENT_IMPORTS;
