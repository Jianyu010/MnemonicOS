agents:
  codex:
    scope:
      - raw/sessions
      - wiki/people
      - wiki/projects
      - wiki/repos
      - wiki/decisions
      - wiki/concepts
      - wiki/incidents
      - wiki/sources
      - wiki/journals
      - wiki/procedures/drafts
      - wiki/review
    can_promote: false
    can_write_canonical: true
    session_prefix: codex

  claude:
    scope:
      - raw/sessions
      - wiki
      - system
    can_promote: true
    can_write_canonical: true
    session_prefix: claude

  human:
    scope:
      - '*'
    can_promote: true
    can_write_canonical: true
