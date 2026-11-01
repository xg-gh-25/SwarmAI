/**
 * Open a shell for a one-click sign-in (design docs/design/2026-11-01-settings-account-merge.md
 * §4.3): at the CURRENT terminal location (the panel shows only that location's shells;
 * one opened elsewhere would be invisible, and revealing the panel would open an empty
 * one), with the command typed in once the shell has started, without Enter.
 *
 * `open` is TerminalContext.openTerminal (it also reveals the panel). Returns the id.
 */
import { terminalLocation } from './terminalLocation';
import { terminalStore, type OpenTerminalOptions } from './TerminalStore';

export function openShellWithCommand(
  open: (opts: OpenTerminalOptions) => string, command: string, title?: string,
): string {
  const here = terminalLocation.snapshot().current;
  const id = open({ cwd: here.cwd, title });
  terminalStore.typeWhenReady(id, command);
  return id;
}
