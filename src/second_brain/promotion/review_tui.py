from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

from ..db import connect_db, run_migrations
from .review_ops import ReviewItem, approve_review_item, list_review_items, reject_review_item

if TYPE_CHECKING:
    from ..config import AppConfig

# ---------------------------------------------------------------------------
# Reason styling
# ---------------------------------------------------------------------------

_REASON_STYLE: dict[str, tuple[str, str, str]] = {
    # reason → (icon, label, rich_style)
    "wiki_conflict":        ("⚠",  "WIKI CONFLICT",        "bold yellow"),
    "type_collision":       ("✖",  "TYPE COLLISION",       "bold red"),
    "low_confidence":       ("○",  "LOW CONFIDENCE",       "dim yellow"),
    "contested_lifecycle":  ("~",  "CONTESTED LIFECYCLE",  "bold orange1"),
}
_REASON_DEFAULT = ("?", "REVIEW REQUIRED", "bold white")


def _reason_label(reason: str) -> tuple[str, str, str]:
    for key, val in _REASON_STYLE.items():
        if reason.startswith(key):
            return val
    return _REASON_DEFAULT


# ---------------------------------------------------------------------------
# Conflict resolution
# ---------------------------------------------------------------------------

@dataclass
class ConflictInfo:
    note_id: str
    title: str
    lifecycle_state: str
    body_path: str | None


def _find_conflict(config: AppConfig, note_id: str) -> ConflictInfo | None:
    """Look up the existing wiki note that conflicts with this review item."""
    conn = connect_db(config.paths.db_path)
    run_migrations(conn, config.paths.workspace_root / "migrations")
    try:
        row = conn.execute(
            "SELECT id, title, status, body_path FROM notes WHERE id = ?",
            (note_id,),
        ).fetchone()
        if row:
            # Try to get lifecycle_state from note file frontmatter
            lifecycle = _read_lifecycle(row["body_path"]) if row["body_path"] else "unknown"
            return ConflictInfo(
                note_id=row["id"],
                title=row["title"],
                lifecycle_state=lifecycle,
                body_path=row["body_path"],
            )
    finally:
        conn.close()
    return None


def _read_lifecycle(body_path: str) -> str:
    try:
        from ..parser import split_frontmatter
        text = Path(body_path).read_text(encoding="utf-8")
        parsed = split_frontmatter(text)
        return str(parsed.metadata.get("lifecycle_state") or "unknown")
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _render_item(
    console: Console,
    item: ReviewItem,
    index: int,
    total: int,
    config: AppConfig,
    full: bool = False,
) -> None:
    console.clear()
    icon, label, style = _reason_label(item.reason)

    # Header rule
    console.print(Rule(
        f"[bold]Review Queue[/bold]  [dim]{index}/{total}[/dim]",
        style="bright_black",
    ))

    # Reason banner
    reason_text = Text(f"  {icon}  {label}  ", style=style)
    console.print(Panel(reason_text, border_style=style.split()[-1], expand=False))

    # Note metadata
    from ..ingest import LIFECYCLE_DEFAULTS
    default_lifecycle = LIFECYCLE_DEFAULTS.get(item.note_type, "draft")
    meta_lines = [
        f"[bold]Title:[/bold]            {item.title}",
        f"[bold]Type:[/bold]             {item.note_type}",
        f"[bold]Note ID:[/bold]          {item.note_id}",
        f"[bold]Created:[/bold]          {item.created_at[:10] if item.created_at else '?'}",
        f"[bold]File:[/bold]             {item.filename}",
        f"[bold]Default lifecycle:[/bold] [cyan]{default_lifecycle}[/cyan]  [dim](if approved without override)[/dim]",
    ]
    console.print(Panel("\n".join(meta_lines), title="Note", border_style="bright_black"))

    # Conflict panel (for wiki_conflict / type_collision)
    if item.reason in ("wiki_conflict", "type_collision"):
        conflict = _find_conflict(config, item.note_id)
        if conflict:
            conflict_lines = [
                f"[bold red]Existing note with this ID is already in the wiki:[/bold red]",
                f"",
                f"  ID:         {conflict.note_id}",
                f"  Title:      {conflict.title}",
                f"  Lifecycle:  {conflict.lifecycle_state}",
                f"  Path:       {conflict.body_path or '(not on disk)'}",
                f"",
                f"[dim]Approve = move review item to same slug → overwrite risk[/dim]",
                f"[dim]Reject  = discard review item, keep existing note[/dim]",
            ]
            console.print(Panel(
                "\n".join(conflict_lines),
                title="⚠  Conflict",
                border_style="yellow",
            ))
        else:
            # File might exist on disk even if not in notes table
            from ..review import slugify
            from ..ingest import TYPE_TO_DIRECTORY
            slug = slugify(item.title)
            dir_ = TYPE_TO_DIRECTORY.get(item.note_type, "sources")
            disk_path = config.paths.vault_root / "wiki" / dir_ / f"{slug}.md"
            if disk_path.exists():
                console.print(Panel(
                    f"[yellow]File exists on disk but not yet indexed:[/yellow]\n  {disk_path}",
                    title="⚠  Conflict",
                    border_style="yellow",
                ))
            else:
                console.print(Panel(
                    f"[dim]Note ID {item.note_id!r} not found in wiki — may be safe to approve.[/dim]",
                    title="Conflict (unresolved)",
                    border_style="bright_black",
                ))

    # Body preview / full
    try:
        from ..parser import split_frontmatter
        from .review_ops import _strip_review_header
        text = item.path.read_text(encoding="utf-8")
        parsed = split_frontmatter(text)
        body = _strip_review_header(parsed.body)
        lines = body.splitlines()
        if full:
            preview = "\n".join(lines)
            title = "Full Note Body"
        else:
            preview = "\n".join(lines[:18])
            if len(lines) > 18:
                preview += f"\n\n[dim]... {len(lines) - 18} more lines (press d for full)[/dim]"
            title = "Body Preview"
        console.print(Panel(preview or "[dim](empty body)[/dim]", title=title, border_style="bright_black"))
    except Exception as exc:
        console.print(Panel(f"[red]Could not parse body: {exc}[/red]", border_style="red"))

    # Key legend  (backslash-escape [ so Rich doesn't treat them as markup tags)
    full_indicator = "[bold]▲ full view[/bold]" if full else "[dim]preview[/dim]"
    legend = (
        r"  [bold green]\[a][/bold green] Approve  "
        r"[bold cyan]\[l][/bold cyan] Approve+lifecycle  "
        r"[bold red]\[r][/bold red] Reject  "
        r"[dim]\[s]/→ Skip[/dim]  "
        r"[dim]\[←] Back[/dim]  "
        r"[dim]\[d] Toggle details[/dim]  "
        r"[dim]\[q] Quit[/dim]"
        f"   {full_indicator}"
    )
    console.print(Rule(style="bright_black"))
    console.print(legend)


# ---------------------------------------------------------------------------
# Lifecycle override prompt
# ---------------------------------------------------------------------------

_LIFECYCLE_CHOICES = ["draft", "verified", "canonical", "contested", "archived"]


def _prompt_lifecycle(console: Console) -> str | None:
    """Ask user to pick a lifecycle state. Returns None on cancel."""
    console.print("\nLifecycle state override:")
    for i, choice in enumerate(_LIFECYCLE_CHOICES, 1):
        console.print(f"  [{i}] {choice}")
    console.print("  [0] Cancel")
    try:
        raw = input("\nChoice: ").strip()
        n = int(raw)
        if n == 0:
            return None
        if 1 <= n <= len(_LIFECYCLE_CHOICES):
            return _LIFECYCLE_CHOICES[n - 1]
    except (ValueError, EOFError):
        pass
    return None


# ---------------------------------------------------------------------------
# Keyboard input
# ---------------------------------------------------------------------------

def _read_key() -> str:
    """Read a single keypress. Returns normalized string."""
    try:
        from prompt_toolkit.input import create_input
        from prompt_toolkit.keys import Keys

        with create_input() as inp:
            keys = inp.read_keys()
            while not keys:
                keys = inp.read_keys()
            k = keys[0].key
            # Normalize arrow keys
            if k == Keys.Right:
                return "right"
            if k == Keys.Left:
                return "left"
            if k == Keys.ControlC:
                return "q"
            return str(k)
    except Exception:
        # Fallback: plain stdin read
        import tty
        import termios
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        return ch


# ---------------------------------------------------------------------------
# Session result
# ---------------------------------------------------------------------------

@dataclass
class ReviewSessionResult:
    approved: int = 0
    rejected: int = 0
    skipped: int = 0
    errors: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_interactive_review(config: AppConfig) -> ReviewSessionResult:
    """Launch blocking interactive review session. Returns session summary."""
    console = Console()

    if not sys.stdin.isatty():
        console.print("[red]Interactive review requires a TTY. Use 'mnemonic review list/approve/reject' instead.[/red]")
        sys.exit(1)

    items = list_review_items(config)
    if not items:
        console.print("[green]No review items pending.[/green]")
        return ReviewSessionResult()

    result = ReviewSessionResult()
    index = 0
    full_view = False

    while index < len(items):
        item = items[index]
        _render_item(console, item, index + 1, len(items), config, full=full_view)

        try:
            key = _read_key()
        except (KeyboardInterrupt, EOFError):
            break

        if key in ("q", "Q"):
            break

        elif key in ("a",):
            try:
                approve_review_item(config, item.filename)
                result.approved += 1
                console.print(f"\n[green]✓ Approved:[/green] {item.title}")
                import time; time.sleep(0.4)
                items.pop(index)
                if index >= len(items):
                    index = len(items)
                full_view = False
            except FileExistsError as exc:
                console.print(f"\n[red]✗ Cannot approve — slug collision:[/red]\n{exc}")
                console.print("[dim]Press any key to continue...[/dim]")
                _read_key()
            except Exception as exc:
                result.errors.append(str(exc))
                console.print(f"\n[red]✗ Error:[/red] {exc}")
                console.print("[dim]Press any key to continue...[/dim]")
                _read_key()

        elif key in ("l",):
            lifecycle = _prompt_lifecycle(console)
            if lifecycle is not None:
                try:
                    approve_review_item(config, item.filename, lifecycle_state=lifecycle)
                    result.approved += 1
                    console.print(f"\n[green]✓ Approved ({lifecycle}):[/green] {item.title}")
                    import time; time.sleep(0.4)
                    items.pop(index)
                    if index >= len(items):
                        index = len(items)
                    full_view = False
                except FileExistsError as exc:
                    console.print(f"\n[red]✗ Cannot approve — slug collision:[/red]\n{exc}")
                    console.print("[dim]Press any key to continue...[/dim]")
                    _read_key()
                except Exception as exc:
                    result.errors.append(str(exc))
                    console.print(f"\n[red]✗ Error:[/red] {exc}")
                    console.print("[dim]Press any key to continue...[/dim]")
                    _read_key()

        elif key in ("r", "R"):
            try:
                reject_review_item(config, item.filename)
                result.rejected += 1
                console.print(f"\n[red]✗ Rejected:[/red] {item.title}")
                import time; time.sleep(0.4)
                items.pop(index)
                if index >= len(items):
                    index = len(items)
                full_view = False
            except Exception as exc:
                result.errors.append(str(exc))
                console.print(f"\n[red]Error:[/red] {exc}")
                console.print("[dim]Press any key to continue...[/dim]")
                _read_key()

        elif key in ("s", "right"):
            result.skipped += 1
            index += 1
            full_view = False  # reset view on navigation

        elif key in ("left",):
            if index > 0:
                index -= 1
            full_view = False  # reset view on navigation

        elif key in ("d",):
            full_view = not full_view  # true toggle

        # Any other key: re-render same item

    # Summary
    console.clear()
    console.print(Rule("[bold]Review Session Complete[/bold]", style="bright_black"))
    console.print(f"  [green]approved:[/green]  {result.approved}")
    console.print(f"  [red]rejected:[/red]  {result.rejected}")
    console.print(f"  [dim]skipped:[/dim]   {result.skipped}")
    if result.errors:
        console.print(f"  [red]errors:[/red]    {len(result.errors)}")
        for e in result.errors:
            console.print(f"    [dim]{e}[/dim]")

    remaining = list_review_items(config)
    if remaining:
        console.print(f"\n  [dim]{len(remaining)} item(s) still pending in wiki/review/[/dim]")

    return result
