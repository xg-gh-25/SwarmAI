/**
 * Full-screen shutdown overlay for SwarmAI desktop app.
 *
 * Listens for the Tauri `shutdown-started` event and displays a modal overlay
 * with a spinner and "Shutting down..." message. Blocks all user interaction
 * with the underlying UI during the shutdown sequence.
 *
 * Key exports:
 * - `ShutdownOverlay` — React component, renders nothing until event fires
 */

import { useEffect, useState } from 'react';
import { listen } from '@tauri-apps/api/event';

export default function ShutdownOverlay() {
  const [shuttingDown, setShuttingDown] = useState(false);

  useEffect(() => {
    const unlisten = listen('shutdown-started', () => {
      setShuttingDown(true);
    });
    return () => {
      unlisten.then((fn) => fn());
    };
  }, []);

  if (!shuttingDown) return null;

  return (
    <div
      data-testid="shutdown-overlay"
      className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/60 pointer-events-auto"
    >
      <div className="flex flex-col items-center gap-4">
        <div className="w-8 h-8 border-[3px] border-white border-t-transparent rounded-full animate-spin" />
        <p className="text-white text-lg font-medium select-none">
          Shutting down…
        </p>
      </div>
    </div>
  );
}
