# Requirements Document

## Introduction

This document specifies the requirements for a complete rebranding of the "Owork" AI Agent platform to "SwarmAI". The rebranding involves updating all references to the product name, identifiers, URLs, visual assets, taglines, and associated metadata across the entire codebase while preserving functionality and preparing the project for submission to a new GitHub repository at https://github.com/xg-gh-25/SwarmAI.git. All references to the original Owork project shall be removed completely.

## Glossary

- **Rebranding_System**: The automated process and tooling used to update brand references across the codebase
- **Brand_Reference**: Any occurrence of the old brand name "Owork" in code, configuration, documentation, or assets
- **App_Identifier**: The unique identifier used by operating systems to identify the application (e.g., com.swarmai.app)
- **Data_Directory**: The filesystem location where the application stores user data, settings, and logs
- **Package_Name**: The name used in package management files (package.json, Cargo.toml, pyproject.toml)
- **Visual_Assets**: Logo images, icons, and other graphical branding elements

## Brand Identity

- **Product Name**: SwarmAI
- **Tagline**: "Your AI Team, 24/7"
- **Tagline (Chinese)**: "专属AI团队，7×24小时协作"
- **Slogan**: "Work Smarter. Stress Less."
- **GitHub Repository**: https://github.com/xg-gh-25/SwarmAI.git
- **Starting Version**: 1.0.0

## Requirements

### Requirement 1: Update Desktop Application Configuration

**User Story:** As a developer, I want all desktop application configuration files updated with the new brand name, so that the built application displays "SwarmAI" branding.

#### Acceptance Criteria

1. WHEN the desktop application is built, THE Rebranding_System SHALL ensure the product name displays as "SwarmAI" in the window title
2. WHEN the desktop application is built, THE Rebranding_System SHALL ensure the app identifier is "com.swarmai.app"
3. WHEN the desktop application is built, THE Rebranding_System SHALL ensure the package name in package.json is "swarmai"
4. WHEN the desktop application is built, THE Rebranding_System SHALL ensure the Cargo.toml package name is "swarmai"
5. WHEN the desktop application is built, THE Rebranding_System SHALL ensure the Rust library name is "swarmai_lib"
6. WHEN the desktop application is built, THE Rebranding_System SHALL ensure the description references "SwarmAI - Your AI Team, 24/7"
7. WHEN the desktop application is built, THE Rebranding_System SHALL ensure the version number is "1.0.0"

### Requirement 2: Update Documentation Files

**User Story:** As a user or contributor, I want all documentation to reference "SwarmAI" consistently with the new brand identity, so that the project identity is clear and professional.

#### Acceptance Criteria

1. WHEN a user reads README.md, THE Rebranding_System SHALL have replaced all "Owork" references with "SwarmAI" and updated the tagline to "Your AI Team, 24/7" (English only)
2. THE Rebranding_System SHALL delete README_EN.md as the project will use English-only documentation in README.md
3. WHEN a user reads CLAUDE.md, THE Rebranding_System SHALL have replaced all "Owork" references with "SwarmAI"
4. WHEN a user reads QUICK_START.md, THE Rebranding_System SHALL have replaced all "Owork" references with "SwarmAI"
5. WHEN a user reads ARCHITECTURE.md, THE Rebranding_System SHALL have replaced all "Owork" references with "SwarmAI"
6. WHEN a user reads SECURITY.md, THE Rebranding_System SHALL have replaced all "Owork" references with "SwarmAI"
7. WHEN a user reads SKILLS_GUIDE.md, THE Rebranding_System SHALL have replaced all "Owork" references with "SwarmAI"
8. WHEN a user reads desktop/README.md or desktop/BUILD_GUIDE.md, THE Rebranding_System SHALL have replaced all "Owork" references with "SwarmAI"
9. WHEN a user reads backend/README.md, THE Rebranding_System SHALL have replaced all "Owork" references with "SwarmAI"
10. WHEN documentation displays the product description, THE Rebranding_System SHALL use the new SwarmAI product description emphasizing the AI team concept

### Requirement 3: Update GitHub Repository References

**User Story:** As a developer, I want all GitHub URLs updated to point to the new repository, so that links and references are accurate.

#### Acceptance Criteria

1. WHEN documentation references the GitHub repository, THE Rebranding_System SHALL use "https://github.com/xg-gh-25/SwarmAI" instead of "https://github.com/xiehust/owork"
2. WHEN CI/CD workflows reference the repository, THE Rebranding_System SHALL use the new repository URL
3. WHEN any configuration file references the old repository, THE Rebranding_System SHALL update it to the new repository

### Requirement 4: Update Visual Assets and Logo

**User Story:** As a product owner, I want the application to have appropriate visual branding assets that reflect the SwarmAI identity, so that the application has a professional and meaningful visual identity.

#### Acceptance Criteria

1. WHEN the application displays a logo or icon, THE Rebranding_System SHALL provide updated visual assets appropriate for SwarmAI (suggesting swarm/team/AI concepts)
2. WHEN the application is built, THE Rebranding_System SHALL ensure icon files (icns, ico, png) are updated with new SwarmAI branding
3. WHEN documentation displays logo images, THE Rebranding_System SHALL update or replace them with new SwarmAI branding assets

### Requirement 5: Update CI/CD Workflow Files

**User Story:** As a developer, I want CI/CD workflows updated with the new brand name, so that build artifacts and releases use correct naming.

#### Acceptance Criteria

1. WHEN the dev-build workflow runs, THE Rebranding_System SHALL ensure artifact names use "SwarmAI" instead of "Owork"
2. WHEN the build-macos workflow runs, THE Rebranding_System SHALL ensure artifact names use "SwarmAI" instead of "Owork"
3. WHEN the build-windows workflow runs, THE Rebranding_System SHALL ensure artifact names use "SwarmAI" instead of "Owork"
4. WHEN the release workflow runs, THE Rebranding_System SHALL ensure release names and artifact names use "SwarmAI" instead of "Owork"

### Requirement 6: Update Data Directory Paths

**User Story:** As a user, I want the application to use appropriately named data directories, so that the application data is organized under the new brand name.

#### Acceptance Criteria

1. WHEN the application stores data on macOS, THE Rebranding_System SHALL ensure the path uses "SwarmAI" (e.g., ~/Library/Application Support/SwarmAI/)
2. WHEN the application stores data on Windows, THE Rebranding_System SHALL ensure the path uses "SwarmAI" (e.g., %LOCALAPPDATA%\SwarmAI\)
3. WHEN documentation references data directories, THE Rebranding_System SHALL update paths to use "SwarmAI"

### Requirement 7: Update Source Code References

**User Story:** As a developer, I want all hardcoded brand references in source code updated, so that the codebase is consistent with the new brand.

#### Acceptance Criteria

1. WHEN source code contains hardcoded "Owork" strings, THE Rebranding_System SHALL replace them with "SwarmAI"
2. WHEN TypeScript/React components display the brand name, THE Rebranding_System SHALL ensure they display "SwarmAI"
3. WHEN Python backend code references the brand name, THE Rebranding_System SHALL ensure it references "SwarmAI"
4. WHEN Rust code references the brand name, THE Rebranding_System SHALL ensure it references "SwarmAI"

### Requirement 8: Update Download URLs and Release References

**User Story:** As a user, I want download links and release references updated or removed, so that documentation does not contain broken or incorrect links.

#### Acceptance Criteria

1. WHEN documentation contains download URLs referencing the old CloudFront distribution, THE Rebranding_System SHALL remove or update these URLs with placeholder text indicating the new download location
2. WHEN documentation references specific release versions with old naming, THE Rebranding_System SHALL update the naming convention to use "SwarmAI"

### Requirement 9: Preserve Project Functionality

**User Story:** As a developer, I want the rebranding to preserve all existing functionality, so that the application works identically after rebranding.

#### Acceptance Criteria

1. WHEN the rebranding is complete, THE Rebranding_System SHALL NOT modify any functional code logic
2. WHEN the rebranding is complete, THE Rebranding_System SHALL NOT change API endpoints or data structures
3. WHEN the rebranding is complete, THE Rebranding_System SHALL NOT alter the application's behavior or features

### Requirement 10: Update Project Tagline and Messaging

**User Story:** As a product owner, I want the project tagline and messaging updated to reflect the SwarmAI brand identity, so that the messaging is consistent and compelling.

#### Acceptance Criteria

1. WHEN documentation displays the tagline, THE Rebranding_System SHALL use "Your AI Team, 24/7" instead of "Personal Office Agent platform"
2. WHEN documentation displays the slogan, THE Rebranding_System SHALL use "Work Smarter. Stress Less."
3. THE Rebranding_System SHALL remove Chinese-specific content as the project uses English-only documentation
4. WHEN the README displays the product description, THE Rebranding_System SHALL incorporate the SwarmAI product description emphasizing AI team collaboration, persistent memory, and supervised agents

### Requirement 11: Clean Up Legacy Assets

**User Story:** As a developer, I want legacy release assets with old naming removed or renamed, so that the repository is clean and consistent.

#### Acceptance Criteria

1. IF legacy DMG files exist with "Owork" naming in the assets folder, THEN THE Rebranding_System SHALL remove them
2. IF any other legacy artifacts exist with old branding, THEN THE Rebranding_System SHALL remove or address them appropriately
3. THE Rebranding_System SHALL remove all references, attributions, or acknowledgments to the original Owork project

### Requirement 12: GitHub Repository Submission Guide

**User Story:** As a first-time GitHub user, I want step-by-step guidance for submitting the rebranded project to my new GitHub repository, so that I can successfully publish the project.

#### Acceptance Criteria

1. WHEN the rebranding is complete, THE Documentation SHALL include step-by-step instructions for initializing a new Git repository
2. WHEN the rebranding is complete, THE Documentation SHALL include instructions for connecting to the remote GitHub repository at https://github.com/xg-gh-25/SwarmAI.git
3. WHEN the rebranding is complete, THE Documentation SHALL include instructions for pushing the code to GitHub
4. WHEN the rebranding is complete, THE Documentation SHALL include instructions for verifying the submission was successful

### Requirement 13: Update Backend Project Name

**User Story:** As a developer, I want the backend project name updated for consistency with the new brand.

#### Acceptance Criteria

1. WHEN the backend pyproject.toml is updated, THE Rebranding_System SHALL change the project name from "agent-platform-backend" to "swarmai-backend"
2. WHEN the backend description is updated, THE Rebranding_System SHALL reference "SwarmAI" appropriately
