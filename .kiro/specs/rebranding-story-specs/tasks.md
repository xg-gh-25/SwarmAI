# Implementation Plan: Owork to SwarmAI Rebranding

## Overview

This implementation plan provides step-by-step tasks for completely rebranding the Owork AI Agent platform to SwarmAI. Tasks are organized in dependency order: configuration first, then source code, documentation, CI/CD, and finally asset cleanup.

## Tasks

- [x] 1. Update desktop application configuration files
  - [x] 1.1 Update desktop/package.json
    - Change `name` from "owork" to "swarmai"
    - Change `version` from "0.0.1" to "1.0.0"
    - _Requirements: 1.3, 1.7_
  
  - [x] 1.2 Update desktop/src-tauri/tauri.conf.json
    - Change `productName` from "Owork" to "SwarmAI"
    - Change `version` from "0.0.1" to "1.0.0"
    - Change `identifier` from "com.owork.desktop" to "com.swarmai.app"
    - Change window `title` from "Owork" to "SwarmAI"
    - _Requirements: 1.1, 1.2, 1.7_
  
  - [x] 1.3 Update desktop/src-tauri/Cargo.toml
    - Change `[package] name` from "owork" to "swarmai"
    - Change `version` from "0.0.1" to "1.0.0"
    - Change `description` to "SwarmAI - Your AI Team, 24/7"
    - Change `authors` from "Owork Team" to "SwarmAI Team"
    - Change `[lib] name` from "owork_lib" to "swarmai_lib"
    - _Requirements: 1.4, 1.5, 1.6, 1.7_
  
  - [x] 1.4 Update desktop/src-tauri/src/main.rs
    - Change `owork_lib::run()` to `swarmai_lib::run()`
    - _Requirements: 1.4, 1.5_
  
  - [x] 1.5 Regenerate desktop/package-lock.json
    - Run `npm install` in desktop/ directory to regenerate with new package name
    - _Requirements: 1.3_

- [x] 2. Update backend configuration
  - [x] 2.1 Update backend/pyproject.toml
    - Change `name` from "agent-platform-backend" to "swarmai-backend"
    - Update `description` to reference SwarmAI
    - _Requirements: 13.1, 13.2_

- [x] 3. Checkpoint - Verify configuration builds
  - Run `cargo check` in desktop/src-tauri/ to verify Rust compiles
  - Run `npm install` in desktop/ to verify npm works
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Update TypeScript/React source code
  - [x] 4.1 Update desktop/src/components/common/Sidebar.tsx
    - Change GitHub URL from "https://github.com/xiehust/owork.git" to "https://github.com/xg-gh-25/SwarmAI.git"
    - _Requirements: 3.1, 7.2_
  
  - [x] 4.2 Update desktop/src/components/common/BackendStartupOverlay.tsx
    - Change data path references from "Owork" to "SwarmAI"
    - Change logo alt text from "Owork" to "SwarmAI"
    - Change app name display from "Owork" to "SwarmAI"
    - _Requirements: 6.1, 6.2, 6.3, 7.2_
  
  - [x] 4.3 Update desktop/src/pages/SettingsPage.tsx
    - Change all data directory path references from "Owork" to "SwarmAI"
    - Change lowercase paths from "owork" to "SwarmAI" (Linux paths)
    - _Requirements: 6.1, 6.2, 6.3, 7.2_
  
  - [x] 4.4 Update desktop/src/i18n/locales/en.json
    - Change dashboard title from "Welcome to Owork" to "Welcome to SwarmAI"
    - _Requirements: 7.2_
  
  - [x] 4.5 Remove Chinese i18n locale file
    - Delete desktop/src/i18n/locales/zh.json (English-only project)
    - Update i18n config to remove Chinese language option
    - _Requirements: 2.2_

- [x] 5. Update Python backend source code
  - [x] 5.1 Update backend/config.py
    - Change all data directory paths from "Owork" to "SwarmAI"
    - Change lowercase paths from "owork" to "swarmai"
    - Update docstring comments
    - _Requirements: 6.1, 6.2, 6.3, 7.3_
  
  - [x] 5.2 Update backend/channels/base.py
    - Change docstring references from "owork" to "SwarmAI"
    - _Requirements: 7.3_
  
  - [x] 5.3 Update backend/core/workspace_manager.py
    - Change ".owork" directory reference to ".swarmai"
    - _Requirements: 7.3_

- [x] 6. Update Rust source code
  - [x] 6.1 Update desktop/src-tauri/src/lib.rs
    - Change comment references from "Owork" to "SwarmAI"
    - Change `OWORK_DEBUG` environment variable to `SWARMAI_DEBUG`
    - _Requirements: 7.4_

- [x] 7. Checkpoint - Verify source code builds
  - Run `npm run build` in desktop/ to verify TypeScript compiles
  - Run `cargo check` in desktop/src-tauri/ to verify Rust compiles
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Update main documentation files
  - [x] 8.1 Replace README.md with English content
    - Delete current Chinese README.md
    - Rename README_EN.md to README.md
    - Replace all "Owork" with "SwarmAI"
    - Update tagline to "Your AI Team, 24/7"
    - Update GitHub URL to "https://github.com/xg-gh-25/SwarmAI"
    - Remove or update CloudFront download URLs
    - Update data directory paths
    - Remove Chinese language toggle badge
    - _Requirements: 2.1, 2.2, 3.1, 8.1, 10.1, 10.4_
  
  - [x] 8.2 Update QUICK_START.md
    - Replace all "Owork" with "SwarmAI"
    - Update GitHub URL
    - Remove or update CloudFront download URLs
    - Update data directory paths
    - Update build artifact names
    - _Requirements: 2.4, 3.1, 6.3, 8.1, 8.2_

- [x] 9. Update secondary documentation files
  - [x] 9.1 Update CLAUDE.md
    - Replace all "Owork" references with "SwarmAI"
    - Update any GitHub URLs
    - _Requirements: 2.3, 3.1_
  
  - [x] 9.2 Update ARCHITECTURE.md
    - Replace all "Owork" references with "SwarmAI"
    - Update project structure references
    - _Requirements: 2.5_
  
  - [x] 9.3 Update SECURITY.md
    - Replace all "Owork" references with "SwarmAI"
    - _Requirements: 2.6_
  
  - [x] 9.4 Update SKILLS_GUIDE.md
    - Replace all "Owork" references with "SwarmAI"
    - _Requirements: 2.7_
  
  - [x] 9.5 Update FLOWCHARTS.md
    - Replace all "Owork" references with "SwarmAI"
    - _Requirements: 2.1_

- [x] 10. Update desktop and backend documentation
  - [x] 10.1 Update desktop/README.md (if exists)
    - Replace all "Owork" references with "SwarmAI"
    - _Requirements: 2.8_
  
  - [x] 10.2 Update desktop/BUILD_GUIDE.md (if exists)
    - Replace all "Owork" references with "SwarmAI"
    - Update build artifact names
    - _Requirements: 2.8_
  
  - [x] 10.3 Update backend/README.md (if exists)
    - Replace all "Owork" references with "SwarmAI"
    - _Requirements: 2.9_

- [x] 11. Update CI/CD workflow files
  - [x] 11.1 Update .github/workflows/build-macos.yml
    - Change artifact names from "Owork-macOS-*" to "SwarmAI-macOS-*"
    - Change app bundle path references from "Owork.app" to "SwarmAI.app"
    - Change release name from "Owork" to "SwarmAI"
    - _Requirements: 5.2, 5.4_
  
  - [x] 11.2 Update .github/workflows/build-windows.yml
    - Change artifact names from "Owork-Windows-*" to "SwarmAI-Windows-*"
    - Change release name from "Owork" to "SwarmAI"
    - _Requirements: 5.3, 5.4_
  
  - [x] 11.3 Update .github/workflows/release.yml
    - Change updater artifact names from "Owork_*" to "SwarmAI_*"
    - Change release name from "Owork" to "SwarmAI"
    - _Requirements: 5.4_
  
  - [x] 11.4 Update .github/workflows/dev-build.yml (if contains Owork references)
    - Change any artifact names from "Owork" to "SwarmAI"
    - _Requirements: 5.1_

- [x] 12. Clean up legacy assets
  - [x] 12.1 Delete assets/Owork_0.0.1-beta_aarch64.dmg
    - Remove the legacy DMG file with old branding
    - _Requirements: 11.1_
  
  - [x] 12.2 Rename desktop/src-tauri/icons/oworklog.png
    - Rename to swarmai-logo.png
    - Update any references to this file
    - _Requirements: 11.2_

- [x] 13. Checkpoint - Final verification
  - Run grep search to verify no "Owork" references remain (excluding .kiro/specs/)
  - Run grep search to verify no old GitHub URL references remain
  - Run grep search to verify no old CloudFront URL references remain
  - Ensure all tests pass, ask the user if questions arise.

- [ ]* 14. Property-based verification tests
  - [ ]* 14.1 Write verification script for Property 1 (No old brand name)
    - **Property 1: No Old Brand Name References**
    - **Validates: Requirements 2.1-2.10, 7.1-7.4**
  
  - [ ]* 14.2 Write verification script for Property 2 (No old GitHub URL)
    - **Property 2: No Old GitHub URL References**
    - **Validates: Requirements 3.1, 3.2, 3.3**
  
  - [ ]* 14.3 Write verification script for Property 3 (No old CloudFront URL)
    - **Property 3: No Old CloudFront URL References**
    - **Validates: Requirements 8.1, 8.2**

- [x] 15. Create GitHub submission guide document
  - Create a GITHUB_SUBMISSION.md file with step-by-step instructions
  - Include: git init, remote add, commit, push commands
  - Include verification steps
  - _Requirements: 12.1, 12.2, 12.3, 12.4_

- [x] 16. Final checkpoint - Ready for GitHub submission
  - Verify all changes are complete
  - Verify application builds successfully
  - Review GitHub submission guide with user
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional verification tests that can be skipped for faster completion
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- The implementation order (config → source → docs → CI/CD → assets) minimizes build breakage
- After completing all tasks, follow the GitHub submission guide in task 15 to push to the new repository
