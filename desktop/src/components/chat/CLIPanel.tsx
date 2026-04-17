/**
 * CLIPanel -- Embedded terminal panel inside the chat area.
 *
 * Renders a real interactive shell (zsh/bash) via xterm.js connected
 * to a backend PTY over WebSocket. Sits between the message area and
 * chat input, taking 1/3 of the content space when open.
 *
 * - Default: hidden
 * - Opens: keyboard shortcut (Cmd+`) or `swarm:open-cli` event
 * - Closes: close button or keyboard shortcut toggle
 *
 * @module CLIPanel
 */

import { useEffect, useRef, useCallback } from 'react';
import { getBackendPort } from '../../services/tauri';

// xterm.js types -- dynamic import to handle missing package gracefully
type Terminal = import('@xterm/xterm').Terminal;

interface CLIPanelProps {
  onClose: () => void;
}

export function CLIPanel({ onClose }: CLIPanelProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const terminalRef = useRef<Terminal | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const cleanupRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    let mounted = true;
    const container = containerRef.current;

    // Dynamic import so the app doesn't crash if xterm isn't installed yet
    const init = async () => {
      try {
        const [xtermMod, fitMod] = await Promise.all([
          import('@xterm/xterm'),
          import('@xterm/addon-fit'),
        ]);

        // Also load CSS dynamically
        if (!document.querySelector('link[data-xterm-css]')) {
          try {
            await import('@xterm/xterm/css/xterm.css');
          } catch {
            // CSS import might fail in some bundler configs -- xterm still works
          }
        }

        if (!mounted || !container) return;

        const term = new xtermMod.Terminal({
          cursorBlink: true,
          fontFamily: "'JetBrains Mono', 'Menlo', 'Monaco', 'Courier New', monospace",
          fontSize: 13,
          lineHeight: 1.2,
          scrollback: 10000,
          allowProposedApi: true,
          theme: {
            background: '#0d1117',
            foreground: '#c9d1d9',
            cursor: '#58a6ff',
            selectionBackground: '#264f78',
            selectionForeground: '#ffffff',
            black: '#484f58',
            red: '#ff7b72',
            green: '#3fb950',
            yellow: '#d29922',
            blue: '#58a6ff',
            magenta: '#bc8cff',
            cyan: '#39c5cf',
            white: '#b1bac4',
            brightBlack: '#6e7681',
            brightRed: '#ffa198',
            brightGreen: '#56d364',
            brightYellow: '#e3b341',
            brightBlue: '#79c0ff',
            brightMagenta: '#d2a8ff',
            brightCyan: '#56d4dd',
            brightWhite: '#f0f6fc',
          },
        });

        const fitAddon = new fitMod.FitAddon();
        term.loadAddon(fitAddon);
        term.open(container);

        // Delay fit to ensure container has dimensions
        requestAnimationFrame(() => {
          if (!mounted) return;
          fitAddon.fit();
        });

        terminalRef.current = term;

        // Connect to backend WebSocket
        const port = getBackendPort();
        const ws = new WebSocket(`ws://localhost:${port}/ws/terminal`);
        wsRef.current = ws;

        ws.binaryType = 'arraybuffer';

        ws.onopen = () => {
          if (!mounted) return;
          ws.send(JSON.stringify({
            type: 'init',
            cols: term.cols,
            rows: term.rows,
          }));
        };

        ws.onmessage = (e: MessageEvent) => {
          if (!mounted) return;
          if (e.data instanceof ArrayBuffer) {
            term.write(new Uint8Array(e.data));
          } else {
            term.write(e.data);
          }
        };

        ws.onclose = () => {
          if (!mounted) return;
          term.write('\r\n\x1b[90m[Terminal disconnected - press any key to reconnect]\x1b[0m\r\n');
        };

        ws.onerror = () => {
          if (!mounted) return;
          term.write('\r\n\x1b[31m[Connection error]\x1b[0m\r\n');
        };

        // Terminal input -> WebSocket
        const dataDisposable = term.onData((data: string) => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(data);
          }
        });

        // Resize -> WebSocket
        const resizeDisposable = term.onResize(({ cols, rows }: { cols: number; rows: number }) => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'resize', cols, rows }));
          }
        });

        // Auto-resize when container changes size
        const observer = new ResizeObserver(() => {
          requestAnimationFrame(() => {
            if (mounted) {
              try {
                fitAddon.fit();
              } catch {
                // Container might be detached
              }
            }
          });
        });
        observer.observe(container);

        // Focus terminal
        term.focus();

        // Store cleanup
        cleanupRef.current = () => {
          observer.disconnect();
          dataDisposable.dispose();
          resizeDisposable.dispose();
          ws.close();
          term.dispose();
          terminalRef.current = null;
          wsRef.current = null;
        };
      } catch (err) {
        // xterm not installed -- show fallback message
        console.error('[CLIPanel] Failed to load xterm.js:', err);
        if (mounted && container) {
          container.innerHTML = `
            <div style="display:flex; align-items:center; justify-content:center; height:100%; color:#6e7681; font-size:13px; font-family:monospace; padding:20px; text-align:center;">
              <div>
                <div style="font-size:24px; margin-bottom:8px;">&#x1F4E6;</div>
                <div>Terminal requires <code>@xterm/xterm</code> package.</div>
                <div style="margin-top:4px; font-size:11px; color:#484f58;">
                  Run: <code>cd desktop && npm install @xterm/xterm @xterm/addon-fit</code>
                </div>
              </div>
            </div>
          `;
        }
      }
    };

    init();

    return () => {
      mounted = false;
      cleanupRef.current?.();
      cleanupRef.current = null;
    };
  }, []);

  const handleClose = useCallback(() => {
    onClose();
  }, [onClose]);

  return (
    <div
      className="flex flex-col border-t border-[var(--color-border)]"
      style={{ flex: '1 1 0%', minHeight: 100 }}
      data-testid="cli-panel"
    >
      {/* Header bar */}
      <div className="flex items-center justify-between px-3 py-0.5 bg-[var(--color-bg-chrome)] border-b border-[var(--color-border)] flex-shrink-0">
        <div className="flex items-center gap-1.5">
          <span className="material-symbols-outlined text-[14px] text-[var(--color-text-muted)]">
            terminal
          </span>
          <span className="text-[11px] font-medium text-[var(--color-text-secondary)]">
            Terminal
          </span>
        </div>
        <button
          onClick={handleClose}
          className="flex items-center justify-center w-5 h-5 rounded hover:bg-[var(--color-hover)] text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
          title="Close terminal (&#8984;`)"
          data-testid="cli-close-btn"
        >
          <span className="material-symbols-outlined text-[14px]">close</span>
        </button>
      </div>

      {/* Terminal container */}
      <div
        ref={containerRef}
        className="flex-1 overflow-hidden"
        style={{ padding: '4px 0 0 4px' }}
      />
    </div>
  );
}

export default CLIPanel;
