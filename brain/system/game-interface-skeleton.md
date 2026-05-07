# MnemonicOS Game Interface Skeleton

## Purpose

There needs to be a game. MnemonicOS should have a 2D top-down exploration RPG
that renders the brain as a walkable library. This is not a content-management
layer — it is a lively interface for exploring and investigating the knowledge
base.

The detailed implementation plan lives in `development/roadmap-library-rpg.md`.
This skeleton records the non-negotiable design decisions so the interface is
built consistently with MnemonicOS semantics.

---

## Core Thesis

- the library is the world; exploration is the primary loop
- the knowledge base is **read-only** from the game layer by default
- the daemon is a bounded service NPC, not a general editor
- "danger" means epistemic uncertainty (stale, contested, risky notes), not
  player damage
- the RPG framing must not distort underlying MnemonicOS semantics

---

## World Map

| Zone | Real source |
|------|-------------|
| Main Library Hall (category shelves) | `brain/wiki/` |
| Procedures wing (Active / Drafts / Retired) | `brain/wiki/procedures/` |
| Incidents shelf | `brain/wiki/incidents/` |
| Review desk | `brain/wiki/review/` |
| Journal corner | `brain/wiki/journals/`, `brain/wiki/overviews/` |
| System wall (pinned notices) | `brain/system/*.md` |
| Dungeon Vault (raw books, grouped by agent + month) | `brain/raw/sessions/` |
| Daemon counter (service desk) | runtime + daemon + `brain/logs/daemon.log` |

Empty categories occupy reserved shelf positions with a "dormant" visual treatment.
Scroll and book counts are live — world reloads on sync/daemon changes.

---

## Non-Negotiable Architecture Decisions

- **Data layer**: reuse `parse_note(path)`, `VaultPaths`, and existing runtime
  models. No game-specific parsing.
- **Procedural tilemaps**: generated from live file counts and note groupings,
  not hand-authored `.tmx` files.
- **Inspector overlays**: stay on top of the current scene — no separate reading
  scene teleport.
- **Read-only by default**: exploration must not mutate canonical notes or raw
  files.
- **Daemon services are explicit verbs**: narrow affordances, visible confirmation,
  no ambient side effects.

---

## Tech Stack (Decided)

- Python + pygame-ce 2.x
- Direct imports from local MnemonicOS codebase
- Entry point: `mnemonicos-game` (already in `pyproject.toml`)
- Implementation: `game/` directory in this repo
- No server required for first playable version

---

## Install and Launch

```bash
pip install -e ".[game]"
mnemonicos-game

# point at a specific workspace
mnemonicos-game --root /absolute/path/to/workspace

# verify without opening the window
mnemonicos-game --check
```

Desktop app bundle (macOS):

```bash
scripts/build_mnemonicos_game_app.sh
# output: dist/MnemonicOSLibraryRPG
```

---

## What Remains To Fill In

Everything else. The implementation plan (`development/roadmap-library-rpg.md`)
has the detailed world design, interaction model, NPC behavior, and trust/freshness
visual language. That document is the authoritative implementation guide; this
skeleton is the architecture boundary.
