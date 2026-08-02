//! One-tap screen capture (app-level Tauri command).
//!
//! Exposes `screen_capture_current_display`: grabs the display the mouse cursor
//! is currently on and writes a PNG the chat attachment pipeline can read.
//!
//! # Why this lives in the signed GUI process (not a child/CLI)
//! macOS TCC Screen-Recording permission is granted to the *responsible* process
//! and does NOT inherit to child processes. The SDK child-process chain
//! (python-backend → bundled `claude` → shell → screencapture) is therefore
//! denied. The Tauri Rust main process IS the signed `.app` that holds the grant,
//! so capture MUST happen here. (See Knowledge/Notes/2026-08-02-macos-screen-audio-capture-for-ai-tauri.md)
//!
//! # Storage location
//! PNGs are written under `~/.swarm-ai/tmp/screenshots/` — inside the frontend's
//! `fs:allow-read` capability scope (`$HOME/.swarm-ai/**`), so the webview can
//! `readFile` the result. `/tmp` is intentionally NOT used (out of fs scope).
//!
//! # Command registration
//! Registered in `lib.rs`'s `generate_handler!` as
//! `screen_capture::screen_capture_current_display` (same app-level pattern as
//! `terminal::pty_*`).

use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

/// Monotonic per-process counter so two captures in the SAME millisecond get
/// distinct filenames (the ms timestamp alone can collide → silent overwrite).
static CAPTURE_SEQ: AtomicU64 = AtomicU64::new(0);

/// Choose which monitor to capture, given the cursor position and each
/// monitor's physical bounds `(x, y, width, height)` (top-left origin, in the
/// same desktop coordinate space the cursor is reported in).
///
/// Returns the index of the FIRST monitor whose rectangle contains the cursor.
/// Returns `None` when the cursor is unavailable or lands on no monitor — the
/// caller falls back to the primary/first display (never silently wrong-crashes).
///
/// Pure + side-effect-free so the multi-monitor selection logic (AC6) is unit
/// tested without constructing a real `xcap::Monitor`.
pub fn select_monitor_index(
    cursor: Option<(i32, i32)>,
    bounds: &[(i32, i32, u32, u32)],
) -> Option<usize> {
    let (cx, cy) = cursor?;
    // saturating_add: a monitor whose bounds couldn't be read is passed in with a
    // sentinel (e.g. x = i32::MIN); `x + w` would then overflow (panic in debug,
    // wrap in release). saturating_add keeps the comparison total + panic-free —
    // an unreadable monitor simply fails to contain the cursor and is skipped.
    bounds.iter().position(|&(x, y, w, h)| {
        cx >= x && cx < x.saturating_add(w as i32) && cy >= y && cy < y.saturating_add(h as i32)
    })
}

/// Directory screenshots are written to (inside the fs:allow-read scope).
fn screenshots_dir() -> Result<PathBuf, String> {
    let home = std::env::var("HOME").map_err(|_| "HOME not set".to_string())?;
    let dir = PathBuf::from(home)
        .join(".swarm-ai")
        .join("tmp")
        .join("screenshots");
    std::fs::create_dir_all(&dir)
        .map_err(|e| format!("failed to create screenshots dir: {e}"))?;
    Ok(dir)
}

/// Delete screenshots older than `max_age_secs` so the tmp dir can't grow
/// unbounded (each capture writes a new timestamped PNG). Best-effort: any
/// unreadable entry is skipped, never fails the capture.
fn prune_old_screenshots(dir: &std::path::Path, max_age_secs: u64) {
    let now = SystemTime::now();
    let Ok(entries) = std::fs::read_dir(dir) else { return };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.extension().and_then(|e| e.to_str()) != Some("png") {
            continue;
        }
        if let Ok(meta) = entry.metadata() {
            if let Ok(modified) = meta.modified() {
                if let Ok(age) = now.duration_since(modified) {
                    if age.as_secs() > max_age_secs {
                        let _ = std::fs::remove_file(&path);
                    }
                }
            }
        }
    }
}

/// Capture the display the mouse cursor is on and return the saved PNG's
/// absolute path. Errors (no permission, no monitor, save failure) are returned
/// as `Err(String)` so the frontend can surface an actionable toast — never a
/// silent black frame passed off as success.
#[tauri::command]
pub async fn screen_capture_current_display(window: tauri::Window) -> Result<String, String> {
    // 1. Cursor position. COORDINATE SPACE (critical for HiDPI/multi-monitor):
    //    Tauri `cursor_position()` returns PHYSICAL pixels, but xcap's monitor
    //    bounds come from macOS `CGDisplayBounds` = LOGICAL points. Comparing the
    //    two directly mis-selects the monitor on any scale≠1 display (Retina,
    //    scaled external). So we convert the cursor to LOGICAL space (÷ scale
    //    factor) to match xcap's bounds before the point-in-rect test.
    //    Residual: a mixed-scale multi-monitor setup where the cursor sits on a
    //    monitor whose scale differs from the window's can still be off by the
    //    ratio — the fallback (primary → first) keeps that safe, never crashes.
    //    May legitimately fail (e.g. no window focus) → cursor unavailable → fallback.
    let scale = window.scale_factor().unwrap_or(1.0);
    let cursor: Option<(i32, i32)> = window.cursor_position().ok().map(|p| {
        ((p.x / scale) as i32, (p.y / scale) as i32)
    });

    // 2. Enumerate monitors via xcap (macOS → ScreenCaptureKit).
    let monitors = xcap::Monitor::all()
        .map_err(|e| format!("could not enumerate monitors (screen-recording permission?): {e}"))?;
    if monitors.is_empty() {
        return Err("no monitors found".to_string());
    }

    // 3. Pick the monitor under the cursor; fall back to primary, then first.
    //    xcap 0.9 geometry/flag accessors each return Result — a monitor that
    //    can't report its bounds is skipped for cursor-matching (defaults keep it
    //    out of any rect), and an unreadable is_primary is treated as non-primary.
    let bounds: Vec<(i32, i32, u32, u32)> = monitors
        .iter()
        .map(|m| {
            (
                m.x().unwrap_or(i32::MIN),
                m.y().unwrap_or(i32::MIN),
                m.width().unwrap_or(0),
                m.height().unwrap_or(0),
            )
        })
        .collect();
    let target_idx = select_monitor_index(cursor, &bounds)
        .or_else(|| monitors.iter().position(|m| m.is_primary().unwrap_or(false)))
        .unwrap_or(0);
    let target = &monitors[target_idx];

    // 4. Capture → RgbaImage. A permission failure surfaces here as Err (not a
    //    black frame) on modern macOS ScreenCaptureKit.
    let image = target
        .capture_image()
        .map_err(|e| format!("capture failed (grant Screen Recording to SwarmAI): {e}"))?;

    // 5. Save PNG to the fs-scoped screenshots dir with a collision-proof name.
    let ts = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);
    let dir = screenshots_dir()?;
    // Bound the tmp dir: drop screenshots older than 24h before writing a new one.
    prune_old_screenshots(&dir, 24 * 60 * 60);
    // ts + monotonic seq → collision-proof even for same-millisecond captures.
    let seq = CAPTURE_SEQ.fetch_add(1, Ordering::Relaxed);
    let path = dir.join(format!("shot-{ts}-{seq}.png"));
    image
        .save(&path)
        .map_err(|e| format!("failed to save screenshot PNG: {e}"))?;

    // 6. Sanity: a real screenshot is never a few bytes. Guards against a
    //    zero/near-empty write masquerading as success.
    match std::fs::metadata(&path) {
        Ok(meta) if meta.len() > 1024 => Ok(path.to_string_lossy().to_string()),
        Ok(_) => Err("screenshot file too small — capture likely blank/denied".to_string()),
        Err(e) => Err(format!("screenshot not readable after save: {e}")),
    }
}

#[cfg(test)]
mod tests {
    use super::select_monitor_index;

    // Two side-by-side 1920x1080 monitors: primary at origin, secondary to its right.
    const TWO_MON: &[(i32, i32, u32, u32)] = &[(0, 0, 1920, 1080), (1920, 0, 1920, 1080)];

    #[test]
    fn cursor_on_primary_selects_index_0() {
        assert_eq!(select_monitor_index(Some((100, 100)), TWO_MON), Some(0));
    }

    #[test]
    fn cursor_on_secondary_selects_index_1() {
        // A point only reachable on the right-hand monitor.
        assert_eq!(select_monitor_index(Some((2500, 500)), TWO_MON), Some(1));
    }

    #[test]
    fn cursor_unavailable_returns_none_for_fallback() {
        assert_eq!(select_monitor_index(None, TWO_MON), None);
    }

    #[test]
    fn cursor_off_all_monitors_returns_none() {
        // Below both monitors' 1080 height — belongs to neither.
        assert_eq!(select_monitor_index(Some((100, 5000)), TWO_MON), None);
    }

    #[test]
    fn boundary_is_half_open_no_double_claim() {
        // x==1920 is the exclusive right edge of monitor 0, inclusive left of 1.
        assert_eq!(select_monitor_index(Some((1920, 0)), TWO_MON), Some(1));
        assert_eq!(select_monitor_index(Some((1919, 0)), TWO_MON), Some(0));
    }

    #[test]
    fn unreadable_monitor_sentinel_does_not_panic_or_overflow() {
        // A monitor whose bounds couldn't be read is passed with i32::MIN sentinels.
        // saturating_add must keep this total (no debug-panic / release-wrap) and
        // the phantom monitor must simply not contain the real cursor.
        let with_bad = &[(i32::MIN, i32::MIN, 1920u32, 1080u32), (0, 0, 1920, 1080)];
        assert_eq!(select_monitor_index(Some((100, 100)), with_bad), Some(1));
        // Even a huge cursor coordinate must not select the sentinel monitor.
        assert_eq!(select_monitor_index(Some((i32::MAX, i32::MAX)), with_bad), None);
    }
}
