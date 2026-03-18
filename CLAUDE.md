# CLAUDE.md

This file provides guidance for AI assistants working in this repository.

## Project Overview

**Letthere_B** is a light computing project. The repository is in its initial state — no source code, dependencies, or configuration files exist yet beyond this documentation.

- **Remote:** http://local_proxy@127.0.0.1:46543/git/rheedoui/Letthere_B
- **Primary branch:** `main` (remote) / `master` (local default)

## Repository Structure

```
Letthere_B/
├── CLAUDE.md       # This file
└── README.md       # Project description
```

## Git Workflow

- Development branches follow the pattern: `claude/<description>-<sessionId>`
- Push with: `git push -u origin <branch-name>`
- Commit messages should be clear and descriptive
- GPG commit signing is enabled via SSH key (`/home/claude/.ssh/commit_signing_key.pub`)

## Development Setup

No dependencies or build tools are configured yet. When the project gains a technology stack, update this section with:
- Language/runtime version requirements
- Dependency installation commands
- Build commands
- Test commands

## Conventions

Since no code exists yet, conventions are not established. When development begins, document here:
- Code style and formatting rules
- Naming conventions
- File/folder organization patterns
- Testing approach

## Notes for AI Assistants

- The project is at an early stage — avoid making assumptions about intended architecture
- Update this file whenever significant conventions, tooling, or structure is established
- Keep changes minimal and focused; avoid over-engineering
- Always work on the correct feature branch before committing
