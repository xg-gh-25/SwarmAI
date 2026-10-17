/**
 * useLinger — keep something open for a moment after it stops being active.
 *
 * Chat display v6 (XG 2026-10-17): a thought, or a work block, folds only 2s
 * after the next item arrives, so the user is never left staring at text that
 * vanished mid-read ("不然这个会让用户等待焦虑").
 *
 * @exports useLinger, FOLD_DELAY_MS
 */
import { useEffect, useState } from 'react';

/** How long finished work stays open after the next item arrives. */
export const FOLD_DELAY_MS = 2000;

/**
 * True while `active`, and for `ms` after it turns false — but only if it was
 * active during this mount (history mounts inactive and stays folded). The
 * transition is caught during render, so the fold never flickers for a frame.
 */
export function useLinger(active: boolean, ms: number = FOLD_DELAY_MS): boolean {
  const [prevActive, setPrevActive] = useState(active);
  const [lingering, setLingering] = useState(false);
  if (prevActive !== active) {
    setPrevActive(active);
    setLingering(!active);
  }
  useEffect(() => {
    if (!lingering) return undefined;
    const id = setTimeout(() => setLingering(false), ms);
    return () => clearTimeout(id);
  }, [lingering, ms]);
  return active || lingering;
}
