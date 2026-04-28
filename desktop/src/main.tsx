/**
 * React entry point for the SwarmAI desktop app.
 *
 * Mounts `<App />` inside `<StrictMode>` into the `#root` DOM element.
 * Includes an idempotency guard that clears stale React trees left behind
 * by Tauri webview reload/restart cycles before calling `createRoot`.
 *
 * @see .kiro/specs/app-restart-chat-layout-collapse/design.md
 */
// Polyfill crypto.randomUUID for non-secure contexts (HTTP, e.g. Hive without HTTPS).
// crypto.randomUUID() requires a secure context (HTTPS or localhost). On plain HTTP
// the function is undefined, crashing any component that generates IDs.
if (typeof crypto !== 'undefined' && typeof crypto.randomUUID !== 'function') {
  crypto.randomUUID = () =>
    '10000000-1000-4000-8000-100000000000'.replace(/[018]/g, (c) =>
      (+c ^ (crypto.getRandomValues(new Uint8Array(1))[0] & (15 >> (+c / 4)))).toString(16),
    ) as `${string}-${string}-${string}-${string}-${string}`;
}

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import '@fontsource-variable/inter';
import '@fontsource/jetbrains-mono/400.css';
import '@fontsource/jetbrains-mono/500.css';
import '@fontsource-variable/material-symbols-outlined';
import './i18n';  // Initialize i18n before App
import App from './App';
import './index.css';

const rootElement = document.getElementById('root');
if (rootElement) {
  // Idempotency guard: clear any stale React trees from prior webview executions.
  // Tauri webview reload re-executes this script without clearing the DOM,
  // causing duplicate h-screen app trees that push content off-screen.
  if (rootElement.hasChildNodes()) {
    console.warn('[main] Stale React tree detected in #root — clearing before remount (Tauri webview reload)');
    rootElement.replaceChildren();
  }
  createRoot(rootElement).render(
    <StrictMode>
      <App />
    </StrictMode>
  );
} else {
  console.error('[main] Fatal: #root element not found in document — app cannot mount');
}
