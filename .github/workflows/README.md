# GitHub Actions Workflows

This directory contains automated build workflows for the Owork desktop application.

## Available Workflows

### 1. Build Windows Release (`build-windows.yml`)

**Purpose**: Build Windows installers only.

**Trigger**:
- Push tags matching `v*` (e.g., `v1.0.0`)
- Manual trigger via GitHub Actions UI

**Outputs**:
- `Owork_*_x64.msi` - Windows Installer (MSI)
- `Owork_*_x64-setup.exe` - NSIS installer

**Artifacts**: Available in GitHub Actions artifacts section after build completes.

---

### 2. Build Multi-Platform Release (`build-release.yml`)

**Purpose**: Build installers for all supported platforms (Windows, macOS, Linux).

**Trigger**:
- Push tags matching `v*` (e.g., `v1.0.0`)
- Manual trigger via GitHub Actions UI

**Build Matrix**:
- **Windows** (windows-latest): MSI + NSIS installers
- **macOS** (macos-latest): DMG + .app bundle
- **Linux** (ubuntu-22.04): DEB + AppImage

**Outputs**:
- Windows: `.msi`, `.exe`
- macOS: `.dmg`, `.app`
- Linux: `.deb`, `.AppImage`

**Release Creation**: Automatically creates a draft GitHub Release when triggered by a tag.

---

## Setup Instructions

### Required GitHub Secrets

Before running these workflows, configure the following secrets in your repository:

1. Go to **Settings** → **Secrets and variables** → **Actions**
2. Add the following secret:

| Secret Name | Description | Required |
|-------------|-------------|----------|
| `ANTHROPIC_API_KEY` | Your Anthropic API key for Claude | No* |

*Not strictly required for building, but needed for testing.

---

## Usage

### Method 1: Create a Release Tag

```bash
# Tag your commit
git tag v1.0.0
git push origin v1.0.0

# The workflow will automatically trigger
```

This will:
1. Build installers for all platforms
2. Create a draft GitHub Release
3. Attach all installers to the release

### Method 2: Manual Trigger

1. Go to **Actions** tab in GitHub
2. Select the workflow you want to run
3. Click **Run workflow**
4. Choose the branch
5. Click **Run workflow** button

---

## Build Output Locations

After successful build, artifacts are available in two places:

### 1. GitHub Actions Artifacts (Always)

- Go to the workflow run page
- Scroll to **Artifacts** section
- Download individual platform artifacts

### 2. GitHub Releases (Tag-triggered builds only)

- Go to **Releases** page
- Find the draft release for your tag
- All installers are attached

---

## Troubleshooting

### Build Fails on Windows

**Issue**: PyInstaller or Python dependency errors

**Solution**: Ensure `requirements.txt` includes all Windows-specific dependencies:
```
pywin32>=305
pyinstaller>=6.0
```

### Build Fails on macOS

**Issue**: Code signing issues

**Solution**: The build works without signing (for development). For distribution, you need:
- Apple Developer account
- Code signing certificate
- Update workflow to include signing steps

### Build Fails on Linux

**Issue**: Missing system dependencies

**Solution**: Dependencies are auto-installed in workflow. If errors persist, check:
- WebKit2GTK version compatibility
- AppImage build requirements

---

## Local Testing

Before pushing tags, test the build locally:

```bash
# On your target platform
cd desktop
npm install
npm run build:all

# Check output in:
# src-tauri/target/release/bundle/
```

---

## Customization

### Change Python Version

Edit the workflow file:
```yaml
- name: Setup Python
  uses: actions/setup-python@v5
  with:
    python-version: '3.12'  # Change version here
```

### Add More Platforms

Add to the `matrix` section in `build-release.yml`:
```yaml
- platform: ubuntu-20.04
  os_name: linux
  target: x86_64-unknown-linux-gnu
```

### Skip Platforms

Comment out unwanted platforms in the matrix:
```yaml
# - platform: macos-latest
#   os_name: macos
#   target: aarch64-apple-darwin
```

---

## Notes

- **Build Time**: Expect 15-30 minutes per platform
- **Parallel Builds**: All platforms build simultaneously
- **Draft Releases**: Review before publishing to users
- **Artifacts Retention**: GitHub keeps artifacts for 90 days by default
