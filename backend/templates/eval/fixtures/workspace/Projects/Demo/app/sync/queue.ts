// What to send: tickets changed since the last successful sync. The queue is saved
// to sync-queue.json so a restart does not lose it; every reader of that file
// expects exactly these fields.
import { promises as fs } from 'fs';

export interface SyncItem { ticketId: string; changedAt: string }

export function pendingItems(changes: SyncItem[], lastSync: string): SyncItem[] {
  return changes.filter((c) => c.changedAt > lastSync);
}

export async function saveQueue(items: SyncItem[], path = 'sync-queue.json'): Promise<void> {
  await fs.writeFile(path, JSON.stringify(items));
}

export async function loadQueue(path = 'sync-queue.json'): Promise<SyncItem[]> {
  return JSON.parse(await fs.readFile(path, 'utf8')) as SyncItem[];
}
