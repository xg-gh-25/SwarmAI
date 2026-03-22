# Contributing to SwarmAI

Thank you for your interest in contributing to SwarmAI! This guide will help you
get started.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Project Structure](#project-structure)
- [Development Workflow](#development-workflow)
- [Code Style](#code-style)
- [Testing](#testing)
- [Submitting Changes](#submitting-changes)
- [Reporting Issues](#reporting-issues)

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](./CODE_OF_CONDUCT.md).
By participating, you are expected to uphold this code.

## Getting Started

### Prerequisites

- **Node.js** 18+ ([nodejs.org](https://nodejs.org/))
- **Python** 3.11+ ([python.org](https://www.python.org/))
- **Rust** (latest stable via [rustup.rs](https://rustup.rs/))
- **uv** (Python package manager: `curl -LsSf https://astral.sh/uv/install.sh | sh`)
- **Git** ([git-scm.com](https://git-scm.com/))

### Development Setup

```bash
# Clone the repository
git clone https://github.com/xg-gh-25/SwarmAI.git
cd SwarmAI

# Frontend setup
cd desktop
npm install

# Backend setup
cd ../backend
uv sync
cp .env.example .env
# Edit .env — set ANTHROPIC_API_KEY or configure AWS Bedrock credentials

# Run in development mode
cd ../desktop
npm run tauri:dev
```

## Project Structure

```
SwarmAI/
├── desktop/                 # Tauri 2.0 + React 19 frontend
│   ├── src/                 # React source code
│   │   ├── pages/           # Main pages (ChatPage, Settings, Skills)
│   │   ├── hooks/           # React hooks (tab state, streaming, attachments)
│   │   ├── services/        # API layer with snake_case ↔ camelCase conversion
│   │   └── components/      # UI components
│   └── src-tauri/           # Rust sidecar management
├── backend/                 # FastAPI Python backend
│   ├── core/                # Core logic (sessions, context, security)
│   ├── routers/             # API endpoints
│   ├── hooks/               # Post-session lifecycle hooks
│   ├── skills/              # Built-in skill definitions
│   └── database/            # SQLite with migrations
└── assets/                  # Images and mockups
```

## Development Workflow

### Running the App

```bash
# Full development mode (frontend + backend via Tauri)
cd desktop && npm run tauri:dev

# Backend only (standalone, port 8000)
cd backend && uv sync && source .venv/bin/activate && python main.py

# Frontend only (Vite dev server)
cd desktop && npm run dev
```

### Building

```bash
# Full production build (frontend + backend + Tauri bundle)
cd desktop && npm run build:all

# Frontend only
cd desktop && npm run build

# Backend only (PyInstaller)
cd desktop && npm run build:backend
```

## Code Style

### Frontend (TypeScript/React)

- **ESLint** for linting: `cd desktop && npm run lint`
- Use `camelCase` for all TypeScript interfaces and variables
- React components use functional components with hooks
- Tailwind CSS for styling — use `bg-[var(--color-*)]` CSS variables, never
  hardcoded colors

### Backend (Python)

- **Ruff** for linting and formatting
- Use `snake_case` for all Python variables and function names
- Pydantic models for data validation
- Type hints required for all function signatures
- Module-level docstrings required for all files

### API Naming Convention

Backend uses `snake_case`, frontend uses `camelCase`. Transformation functions
in `desktop/src/services/*.ts` handle conversion. When adding new fields, update
both the Pydantic model and the corresponding `toCamelCase()` function.

## Testing

### Frontend Tests

```bash
cd desktop

# Run all tests (single execution)
npm test -- --run

# Run specific test file
npx vitest run src/hooks/__tests__/mytest.test.ts
```

- **Vitest** + **React Testing Library** for unit tests
- **fast-check** for property-based tests
- Test files go in `__tests__/` directories next to source files

### Backend Tests

```bash
cd backend

# Run all tests
pytest

# Run specific test file
pytest tests/test_mymodule.py -v

# Run with coverage
pytest --cov=. --cov-report=term-missing
```

- **pytest** + **pytest-asyncio** for async tests
- **Hypothesis** for property-based tests
- Test files go in `backend/tests/`

## Submitting Changes

### Pull Request Process

1. Fork the repository
2. Create a feature branch: `git checkout -b feat/my-feature`
3. Make your changes with clear, atomic commits
4. Ensure all tests pass
5. Update documentation if needed
6. Submit a Pull Request

### Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(component): add new feature
fix(backend): resolve session timeout issue
docs: update contributing guide
test: add property-based tests for context loader
```

Types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `chore`, `ci`

### PR Guidelines

- Keep PRs focused — one feature or fix per PR
- Include a clear description of what changed and why
- Add tests for new functionality
- Update relevant documentation
- Ensure CI passes before requesting review

## Reporting Issues

### Bug Reports

Use the [GitHub Issues](https://github.com/xg-gh-25/SwarmAI/issues) page.
Include:

- SwarmAI version
- Operating system and version
- Steps to reproduce
- Expected vs actual behavior
- Relevant logs (`~/.swarm-ai/logs/backend.log`)

### Feature Requests

Open a GitHub Issue with the `enhancement` label. Describe:

- The problem you're trying to solve
- Your proposed solution
- Any alternatives you've considered

## License

SwarmAI is dual-licensed under **AGPL v3** and a **Commercial License**.

By contributing to SwarmAI, you agree that:

1. Your contributions will be licensed under the [AGPL v3](./LICENSE-AGPL)
2. You grant the project maintainers the right to offer your contributions
   under the commercial license as well

This is necessary to maintain the dual-licensing model. We may ask contributors
to sign a Contributor License Agreement (CLA) in the future to formalize this.
