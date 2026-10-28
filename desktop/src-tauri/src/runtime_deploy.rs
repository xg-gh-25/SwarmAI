//! Versioned backend runtime: deploy the installer's bundled backend to
//! `<runtime root>/<version>/` and point `current` at it (design
//! docs/design/2026-10-24-swarmai-usable-by-anyone.md §3.1).
//!
//! The daemon never runs the copy inside the app bundle (an update replaces those
//! files while the process runs). Every platform uses the same layout:
//!
//! ```text
//! <root>/                     macOS/Linux ~/.swarm-ai/runtime, Windows %LOCALAPPDATA%\SwarmAI\runtime
//!   <version>/                python-backend[.exe], _internal/, resources/, .version, .deployed
//!   current                   text file: the version the daemon starts
//!   previous                  text file: the one rollback version
//! ```
//!
//! - `deploy` copies `<resource_dir>/python-backend` (and the resource files the backend
//!   reads, into `resources/`) into a staging dir, checks every backend file against the
//!   installer's `install-manifest.json` (sha256), then renames it into place. A version
//!   dir whose `.deployed` marker names the same manifest is reused as is.
//! - `switch_current` writes `current` atomically (tmp + rename), moves the old value to
//!   `previous`, and prunes every other version dir.
//! - `rollback` points `current` back at `previous`.
//!
//! Pointer files, not symlinks: the same on Windows (no symlink privilege needed) and
//! readable from the launchd wrapper with one `cat`.
//!
//! - `bundled_build_differs` tells a same-version rebuild of the app (a new manifest)
//!   from the build the runtime already runs. Deploying such a build over the running
//!   version moves the running copy to `<version>.app-prev` and makes that `previous`,
//!   so a failed upgrade still has a copy to roll back to.
//!
//! Public: `runtime_root`, `exe_name`, `deploy`, `switch_current`, `rollback`,
//! `current_version`, `current_exe`, `bundled_build_differs`, `RuntimeError`.

use sha2::{Digest, Sha256};
use std::fs;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};

#[derive(Debug)]
pub struct RuntimeError(pub String);

impl std::fmt::Display for RuntimeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl From<std::io::Error> for RuntimeError {
    fn from(e: std::io::Error) -> Self {
        RuntimeError(e.to_string())
    }
}

type Result<T> = std::result::Result<T, RuntimeError>;

pub fn exe_name() -> &'static str {
    if cfg!(windows) { "python-backend.exe" } else { "python-backend" }
}

/// The runtime root for this user (see the module docs).
pub fn runtime_root() -> Result<PathBuf> {
    if cfg!(windows) {
        let base = std::env::var("LOCALAPPDATA").ok().filter(|s| !s.is_empty())
            .map(PathBuf::from)
            .or_else(|| std::env::var("USERPROFILE").ok().filter(|s| !s.is_empty())
                .map(|h| PathBuf::from(h).join("AppData").join("Local")))
            .ok_or_else(|| RuntimeError("LOCALAPPDATA and USERPROFILE not set".into()))?;
        Ok(base.join("SwarmAI").join("runtime"))
    } else {
        let home = std::env::var("HOME").ok().filter(|s| !s.is_empty())
            .ok_or_else(|| RuntimeError("HOME not set".into()))?;
        Ok(PathBuf::from(home).join(".swarm-ai").join("runtime"))
    }
}

fn valid_version(v: &str) -> bool {
    !v.is_empty() && v.len() <= 64 && !v.starts_with('.')
        && v.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '-' | '+' | '_'))
}

fn sha256_file(p: &Path) -> Result<String> {
    let mut f = fs::File::open(p)?;
    let mut h = Sha256::new();
    let mut buf = vec![0u8; 1 << 20];
    loop {
        let n = f.read(&mut buf)?;
        if n == 0 {
            break;
        }
        h.update(&buf[..n]);
    }
    Ok(format!("{:x}", h.finalize()))
}

fn copy_tree(src: &Path, dst: &Path) -> Result<()> {
    fs::create_dir_all(dst)?;
    for entry in fs::read_dir(src)? {
        let entry = entry?;
        let ty = entry.file_type()?;
        let to = dst.join(entry.file_name());
        if ty.is_dir() {
            copy_tree(&entry.path(), &to)?;
        } else if ty.is_symlink() {
            #[cfg(unix)]
            std::os::unix::fs::symlink(fs::read_link(entry.path())?, &to)?;
            #[cfg(not(unix))]
            fs::copy(entry.path(), &to).map(|_| ())?;
        } else {
            fs::copy(entry.path(), &to)?;
        }
    }
    Ok(())
}

fn write_atomic(path: &Path, text: &str) -> Result<()> {
    let tmp = path.with_extension("tmp");
    {
        let mut f = fs::File::create(&tmp)?;
        f.write_all(text.as_bytes())?;
        f.sync_all()?;
    }
    fs::rename(&tmp, path)?;
    Ok(())
}

fn read_pointer(path: &Path) -> Option<String> {
    let v = fs::read_to_string(path).ok()?.trim().to_string();
    valid_version(&v).then_some(v)
}

/// `(manifest sha256, [(path under python-backend/, sha256)])`, or None for a build
/// without a manifest (a local dev build): then only the executable is required.
fn manifest(resource_dir: &Path) -> Result<Option<(String, Vec<(String, String)>)>> {
    let path = resource_dir.join("install-manifest.json");
    if !path.is_file() {
        return Ok(None);
    }
    let digest = sha256_file(&path)?;
    let raw: serde_json::Value = serde_json::from_str(&fs::read_to_string(&path)?)
        .map_err(|e| RuntimeError(format!("install-manifest.json unreadable: {}", e)))?;
    let mut files = Vec::new();
    for f in raw.get("files").and_then(|v| v.as_array()).into_iter().flatten() {
        let (Some(p), Some(s)) = (f.get("path").and_then(|v| v.as_str()),
                                   f.get("sha256").and_then(|v| v.as_str())) else { continue };
        if let Some(rel) = p.strip_prefix("python-backend/") {
            if rel.split('/').any(|c| c == ".." || c.is_empty()) {
                return Err(RuntimeError(format!("install-manifest.json: bad path {}", p)));
            }
            files.push((rel.to_string(), s.to_string()));
        }
    }
    Ok(Some((digest, files)))
}

/// Deploy the bundled backend for `version` under `root`; returns the version dir.
pub fn deploy(resource_dir: &Path, root: &Path, version: &str) -> Result<PathBuf> {
    if !valid_version(version) {
        return Err(RuntimeError(format!("invalid version {:?}", version)));
    }
    let src = resource_dir.join("python-backend");
    if !src.join(exe_name()).is_file() {
        return Err(RuntimeError(format!("the installer has no backend: {} missing",
                                        src.join(exe_name()).display())));
    }
    let man = manifest(resource_dir)?;
    let marker = match &man {
        Some((digest, _)) => format!("manifest {}", digest),
        None => "no-manifest".to_string(),
    };
    let dest = root.join(version);
    if man.is_some() && dest.join(exe_name()).is_file()
        && fs::read_to_string(dest.join(".deployed")).map(|m| m.trim() == marker).unwrap_or(false)
    {
        return Ok(dest);      // this exact build is already deployed
    }
    fs::create_dir_all(root)?;
    let staging = root.join(format!(".staging-{}", version));
    if staging.exists() {
        fs::remove_dir_all(&staging)?;
    }
    copy_tree(&src, &staging)?;
    // the resource files the backend reads next to its executable (bundle_paths)
    let res = staging.join("resources");
    fs::create_dir_all(&res)?;
    for entry in fs::read_dir(resource_dir)? {
        let entry = entry?;
        let name = entry.file_name();
        let n = name.to_string_lossy();
        // on Windows the resource dir is the install dir: skip the app's own exe / dlls
        let lower = n.to_ascii_lowercase();
        if entry.file_type()?.is_file() && !n.starts_with('.') && !lower.ends_with(".icns")
            && !lower.ends_with(".exe") && !lower.ends_with(".dll")
            && n != "install-manifest.json"
        {
            fs::copy(entry.path(), res.join(&name))?;
        }
    }
    if let Some((_, files)) = &man {
        for (rel, want) in files {
            let got = sha256_file(&staging.join(rel))
                .map_err(|e| RuntimeError(format!("{}: {}", rel, e)))?;
            if &got != want {
                let _ = fs::remove_dir_all(&staging);
                return Err(RuntimeError(format!("{} does not match the install manifest", rel)));
            }
        }
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(staging.join(exe_name()), fs::Permissions::from_mode(0o755))?;
    }
    fs::write(staging.join(".version"), format!("{} release", version))?;
    fs::write(staging.join(".deployed"), &marker)?;
    // a new build of the version that runs now keeps the running copy as the rollback
    // version `<version>.app-prev` (its own name: dev tooling owns `<version>.prev`).
    // Only for a manifest build over a working copy: a broken dir or a dev build is
    // replaced as before, so it never displaces the real `previous`.
    let keep = (man.is_some() && dest.join(exe_name()).is_file()
                && current_version(root).as_deref() == Some(version))
        .then(|| format!("{}.app-prev", version));
    if let Some(k) = &keep {
        let _ = fs::remove_dir_all(root.join(k));
        fs::rename(&dest, root.join(k))?;
    } else if dest.exists() {
        fs::remove_dir_all(&dest)?;
    }
    if let Err(e) = fs::rename(&staging, &dest) {
        if let Some(k) = &keep {
            let _ = fs::rename(root.join(k), &dest);    // `current` must keep a copy
        }
        return Err(e.into());
    }
    if let Some(k) = &keep {
        write_atomic(&root.join("previous"), k)?;
    }
    Ok(dest)
}

/// Whether this app's bundled backend should replace the one `current` runs even
/// though the versions match: a rebuilt app of the same version carries a new build,
/// and comparing versions alone never deploys it. True only when the bundle has a
/// manifest AND the runtime was deployed by an app (its `.deployed` names a manifest)
/// AND the two differ AND the bundle is not the build a rollback just left (`previous`
/// carries its marker; so reinstalling that earlier build on purpose does not redeploy
/// it either). A runtime with no marker came from dev tooling (`prod.sh`,
/// whose rsync does not carry one) and is left alone, so an installed app never puts
/// its bundle over a newer dev deploy; a bundle without a manifest (a local dev build
/// of the app) never redeploys.
pub fn bundled_build_differs(resource_dir: &Path, root: &Path) -> bool {
    let Ok(Some((digest, _))) = manifest(resource_dir) else { return false };
    let Some(version) = current_version(root) else { return false };
    let want = format!("manifest {}", digest);
    let marker_of = |v: &str| fs::read_to_string(root.join(v).join(".deployed"))
        .map(|m| m.trim().to_string()).ok();
    // this build already ran and was rolled back from (it is `previous`): retrying it
    // on every launch would restart a working daemon each time
    if read_pointer(&root.join("previous")).and_then(|p| marker_of(&p)).as_deref()
        == Some(want.as_str())
    {
        return false;
    }
    match marker_of(&version) {
        Some(m) => m.starts_with("manifest ") && m != want,
        None => false,
    }
}

pub fn current_version(root: &Path) -> Option<String> {
    read_pointer(&root.join("current"))
}

pub fn current_exe(root: &Path) -> Option<PathBuf> {
    let exe = root.join(current_version(root)?).join(exe_name());
    exe.is_file().then_some(exe)
}

/// Point `current` at `version` (already deployed), keep the old one as `previous`,
/// and remove every other version dir.
pub fn switch_current(root: &Path, version: &str) -> Result<()> {
    if !root.join(version).join(exe_name()).is_file() {
        return Err(RuntimeError(format!("{} is not deployed", version)));
    }
    let old = current_version(root);
    if let Some(o) = old.as_deref().filter(|o| *o != version) {
        write_atomic(&root.join("previous"), o)?;
    }
    write_atomic(&root.join("current"), version)?;
    let keep = [Some(version.to_string()), read_pointer(&root.join("previous"))];
    for entry in fs::read_dir(root)? {
        let entry = entry?;
        let name = entry.file_name().to_string_lossy().to_string();
        if entry.file_type()?.is_dir() && !name.starts_with('.')
            && !keep.iter().flatten().any(|k| *k == name)
        {
            let _ = fs::remove_dir_all(entry.path());
        }
    }
    Ok(())
}

/// Point `current` back at `previous`; returns the version now current.
pub fn rollback(root: &Path) -> Result<String> {
    let prev = read_pointer(&root.join("previous"))
        .ok_or_else(|| RuntimeError("no previous runtime to roll back to".into()))?;
    if !root.join(&prev).join(exe_name()).is_file() {
        return Err(RuntimeError(format!("previous runtime {} is gone", prev)));
    }
    if let Some(cur) = current_version(root) {
        write_atomic(&root.join("previous"), &cur)?;
    }
    write_atomic(&root.join("current"), &prev)?;
    Ok(prev)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("swarm-rt-{}-{}", name, std::process::id()));
        let _ = fs::remove_dir_all(&d);
        fs::create_dir_all(&d).unwrap();
        d
    }

    fn bundle(dir: &Path, body: &[u8], with_manifest: bool) {
        let be = dir.join("python-backend");
        fs::create_dir_all(be.join("_internal")).unwrap();
        fs::write(be.join(exe_name()), body).unwrap();
        fs::write(be.join("_internal").join("lib.so"), b"lib").unwrap();
        fs::write(dir.join("seed.db"), b"seed").unwrap();
        if with_manifest {
            let files = serde_json::json!({"files": [
                {"path": format!("python-backend/{}", exe_name()),
                 "sha256": sha256_file(&be.join(exe_name())).unwrap()},
                {"path": "python-backend/_internal/lib.so",
                 "sha256": sha256_file(&be.join("_internal/lib.so")).unwrap()},
                {"path": "seed.db", "sha256": sha256_file(&dir.join("seed.db")).unwrap()}]});
            fs::write(dir.join("install-manifest.json"), files.to_string()).unwrap();
        }
    }

    #[test]
    fn deploy_switch_reuse_and_rollback() {
        let base = tmp("flow");
        let (res, root) = (base.join("res"), base.join("runtime"));
        bundle(&res, b"v1", true);
        let d1 = deploy(&res, &root, "1.0.0").unwrap();
        assert!(d1.join(exe_name()).is_file() && d1.join("resources/seed.db").is_file());
        switch_current(&root, "1.0.0").unwrap();
        assert_eq!(current_exe(&root).unwrap(), d1.join(exe_name()));
        // same build again: reused, not recopied
        fs::write(d1.join("touched"), b"x").unwrap();
        deploy(&res, &root, "1.0.0").unwrap();
        assert!(d1.join("touched").is_file());
        // upgrade, then rollback
        bundle(&res, b"v2", true);
        deploy(&res, &root, "2.0.0").unwrap();
        switch_current(&root, "2.0.0").unwrap();
        assert_eq!(current_version(&root).as_deref(), Some("2.0.0"));
        assert_eq!(rollback(&root).unwrap(), "1.0.0");
        assert_eq!(current_version(&root).as_deref(), Some("1.0.0"));
        // a third version prunes everything but current + previous
        deploy(&res, &root, "3.0.0").unwrap();
        switch_current(&root, "3.0.0").unwrap();
        assert!(root.join("1.0.0").is_dir() && root.join("3.0.0").is_dir());
        assert!(!root.join("2.0.0").exists());
        let _ = fs::remove_dir_all(&base);
    }

    #[test]
    fn a_same_version_rebuild_is_detected_only_over_an_app_deploy() {
        let base = tmp("rebuild");
        let (res, root) = (base.join("res"), base.join("runtime"));
        bundle(&res, b"v1", true);
        deploy(&res, &root, "1.0.0").unwrap();
        switch_current(&root, "1.0.0").unwrap();
        // the build that is running: nothing to do
        assert!(!bundled_build_differs(&res, &root));
        // the app is rebuilt with the same version: its bundle is a new build
        bundle(&res, b"v1-rebuilt", true);
        assert!(bundled_build_differs(&res, &root));
        // deploying it makes them match again; the running copy stays as the rollback
        deploy(&res, &root, "1.0.0").unwrap();
        switch_current(&root, "1.0.0").unwrap();
        assert_eq!(fs::read(root.join("1.0.0").join(exe_name())).unwrap(), b"v1-rebuilt");
        assert!(!bundled_build_differs(&res, &root));
        assert_eq!(rollback(&root).unwrap(), "1.0.0.app-prev");
        assert_eq!(fs::read(root.join("1.0.0.app-prev").join(exe_name())).unwrap(), b"v1");
        // the rolled-back build is not retried on the next launch
        assert!(!bundled_build_differs(&res, &root));
        switch_current(&root, "1.0.0").unwrap();
        // a dev deploy (prod.sh) leaves no marker: an app never overwrites it
        fs::remove_file(root.join("1.0.0/.deployed")).unwrap();
        bundle(&res, b"v1-older-app", true);
        assert!(!bundled_build_differs(&res, &root));
        // an app build without a manifest never redeploys; nor without a runtime
        fs::write(root.join("1.0.0/.deployed"), "manifest something-else").unwrap();
        fs::remove_file(res.join("install-manifest.json")).unwrap();
        assert!(!bundled_build_differs(&res, &root));
        bundle(&res, b"v1", true);
        assert!(!bundled_build_differs(&res, &base.join("no-runtime")));
        let _ = fs::remove_dir_all(&base);
    }

    #[test]
    fn a_broken_or_dev_copy_is_replaced_without_displacing_previous() {
        let base = tmp("repair");
        let (res, root) = (base.join("res"), base.join("runtime"));
        bundle(&res, b"old", true);
        deploy(&res, &root, "0.9.0").unwrap();
        switch_current(&root, "0.9.0").unwrap();
        bundle(&res, b"v1", true);
        deploy(&res, &root, "1.0.0").unwrap();
        switch_current(&root, "1.0.0").unwrap();
        // the running copy lost its executable: a repair deploy replaces it in place
        fs::remove_file(root.join("1.0.0").join(exe_name())).unwrap();
        deploy(&res, &root, "1.0.0").unwrap();
        switch_current(&root, "1.0.0").unwrap();
        assert!(!root.join("1.0.0.app-prev").exists());
        assert_eq!(read_pointer(&root.join("previous")).as_deref(), Some("0.9.0"));
        // a build without a manifest redeploys each time; it never rotates `previous`
        fs::remove_file(res.join("install-manifest.json")).unwrap();
        deploy(&res, &root, "1.0.0").unwrap();
        switch_current(&root, "1.0.0").unwrap();
        assert!(!root.join("1.0.0.app-prev").exists());
        assert_eq!(read_pointer(&root.join("previous")).as_deref(), Some("0.9.0"));
        assert!(root.join("0.9.0").join(exe_name()).is_file());
        let _ = fs::remove_dir_all(&base);
    }

    #[test]
    fn a_file_that_does_not_match_the_manifest_is_refused() {
        let base = tmp("bad");
        let (res, root) = (base.join("res"), base.join("runtime"));
        bundle(&res, b"v1", true);
        fs::write(res.join("python-backend/_internal/lib.so"), b"tampered").unwrap();
        let err = deploy(&res, &root, "1.0.0").unwrap_err();
        assert!(err.0.contains("does not match"), "{}", err);
        assert!(!root.join("1.0.0").exists() && !root.join(".staging-1.0.0").exists());
        let _ = fs::remove_dir_all(&base);
    }

    #[test]
    fn no_backend_or_bad_version_is_an_error_and_switch_needs_a_deploy() {
        let base = tmp("none");
        let root = base.join("runtime");
        assert!(deploy(&base, &root, "1.0.0").unwrap_err().0.contains("no backend"));
        let res = base.join("res");
        bundle(&res, b"v1", false);
        assert!(deploy(&res, &root, "../x").is_err());
        assert!(deploy(&res, &root, "1.0.0").is_ok());       // dev build: no manifest
        assert!(switch_current(&root, "9.9.9").is_err());
        assert!(rollback(&root).is_err());
        let _ = fs::remove_dir_all(&base);
    }
}
