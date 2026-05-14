from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json
import sqlite3
import struct
import sys
import tempfile
import textwrap
import unittest
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from second_brain.config import load_config
from second_brain.db import connect_db, run_migrations
from second_brain.ingest import ingest_artifact, reprocess_artifact, reprocess_failed_artifacts
from second_brain.projection import ProjectionOptions, ProjectionService, SourceRef
from second_brain.projection.markers import format_marker, parse_markers
from second_brain.retrieval import retrieve


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n")


def _config(workspace: Path) -> Path:
    config_path = workspace / "config.toml"
    _write(
        config_path,
        f"""
        [paths]
        vault_root = "{(workspace / 'brain').as_posix()}"
        db_path = "{(workspace / 'brain/data/archive.db').as_posix()}"
        vectors_dir = "{(workspace / 'brain/data/vectors').as_posix()}"
        graph_dir = "{(workspace / 'brain/data/graph').as_posix()}"
        eval_queries = "{(workspace / 'evals/queries.jsonl').as_posix()}"

        [ingest]
        auto_trigger_pipeline = false
        lazy_pdf_ingest = false
        """,
    )
    return config_path


def _png_bytes() -> bytes:
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return signature + b"\x00\x00\x00\rIHDR" + ihdr + b"\x00\x00\x00\x00"


def _write_docx_with_image(path: Path) -> None:
    document_xml = """
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body>
        <w:p><w:r><w:t>Figure 1 shows the intake shape.</w:t></w:r></w:p>
      </w:body>
    </w:document>
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", textwrap.dedent(document_xml).strip())
        archive.writestr("word/media/image1.png", _png_bytes())


class WestFlangeTest(unittest.TestCase):
    def test_text_artifact_persists_metadata_without_archive_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = load_config(_config(workspace), workspace)
            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            connection.close()

            source = workspace / "sample.txt"
            source.write_text("West flange raw observation should be ephemeral usable raw.\n")
            result = ingest_artifact(config, path=source, tags=["west"])

            self.assertEqual(result.ingest_status, "accepted")
            self.assertEqual(result.source_format, "plain_text")
            self.assertTrue(Path(result.raw_path).exists())
            manifest = json.loads(Path(result.manifest_path).read_text())
            self.assertEqual(manifest["raw_sha256"], sqlite3.connect(":memory:").execute("SELECT lower(?)", (manifest["raw_sha256"],)).fetchone()[0])
            self.assertNotIn("usable_raw_text", manifest)

            db = connect_db(config.paths.db_path)
            try:
                row = db.execute("SELECT chunk_count, ingest_status, raw_sha256 FROM archive WHERE id = ?", (result.archive_id,)).fetchone()
                self.assertEqual(int(row["chunk_count"]), 0)
                self.assertEqual(row["ingest_status"], "accepted")
                chunk_count = db.execute("SELECT COUNT(*) AS n FROM archive_chunks WHERE archive_id = ?", (result.archive_id,)).fetchone()
                self.assertEqual(int(chunk_count["n"]), 0)
            finally:
                db.close()

            retrieval = retrieve(config, "ephemeral usable raw", top_k=5)
            self.assertFalse(any(result.archive_id in hit.id for hit in retrieval.hits))

    def test_failed_pdf_is_retained_with_manual_projection_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = load_config(_config(workspace), workspace)
            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            connection.close()

            source = workspace / "image-only.pdf"
            source.write_bytes(b"%PDF-1.4\n1 0 obj << /Type /Page /Subtype /Image >> endobj\n%%EOF\n")
            result = ingest_artifact(config, path=source)

            self.assertEqual(result.ingest_status, "failed_extraction")
            self.assertEqual(result.failure_reason, "no_text_extracted")
            self.assertTrue(result.needs_manual_projection)
            self.assertTrue(result.image_only_pdf)
            self.assertTrue(Path(result.raw_path).exists())

            manifest = json.loads(Path(result.manifest_path).read_text())
            self.assertEqual(manifest["ingest_status"], "failed_extraction")
            self.assertTrue(manifest["needs_manual_projection"])

    def test_docx_embedded_image_metadata_is_vlm_ready_without_persisted_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = load_config(_config(workspace), workspace)
            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            connection.close()

            source = workspace / "figures.docx"
            _write_docx_with_image(source)
            result = ingest_artifact(config, path=source)

            self.assertEqual(result.ingest_status, "accepted")
            self.assertTrue(result.needs_visual_pass)
            self.assertEqual(result.figure_count, 1)
            artifact_dir = Path(result.raw_path).parent
            self.assertFalse((artifact_dir / "figures").exists())

            db = connect_db(config.paths.db_path)
            try:
                row = db.execute(
                    """
                    SELECT storage_policy, attachment_path, width_px, height_px, referenced_by_label, needs_visual_pass
                    FROM artifact_figures
                    WHERE archive_id = ?
                    """,
                    (result.archive_id,),
                ).fetchone()
                self.assertEqual(row["storage_policy"], "in_memory_extracted")
                self.assertIsNone(row["attachment_path"])
                self.assertEqual(int(row["width_px"]), 1)
                self.assertEqual(int(row["height_px"]), 1)
                self.assertEqual(int(row["referenced_by_label"]), 1)
                self.assertEqual(int(row["needs_visual_pass"]), 1)
            finally:
                db.close()

    def test_source_attachment_is_archived_as_true_raw_attachment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = load_config(_config(workspace), workspace)
            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            connection.close()

            source = workspace / "paper.md"
            source.write_text("# Paper\n\nSee Figure 1.\n")
            attachment = workspace / "figure1.png"
            attachment.write_bytes(_png_bytes())
            result = ingest_artifact(config, path=source, attachments=[attachment])

            self.assertEqual(result.ingest_status, "accepted")
            self.assertEqual(result.figure_count, 1)
            attachment_copy = Path(result.raw_path).parent / "attachments" / "figure1.png"
            self.assertTrue(attachment_copy.exists())

            db = connect_db(config.paths.db_path)
            try:
                row = db.execute(
                    "SELECT storage_policy, attachment_path, sha256 FROM artifact_figures WHERE archive_id = ?",
                    (result.archive_id,),
                ).fetchone()
                self.assertEqual(row["storage_policy"], "source_attachment")
                self.assertEqual(row["attachment_path"], str(attachment_copy.resolve()))
                self.assertTrue(row["sha256"])
            finally:
                db.close()

    def test_source_attachments_with_same_basename_do_not_collide(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = load_config(_config(workspace), workspace)
            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            connection.close()

            source = workspace / "paper.md"
            source.write_text("# Paper\n\nSee both figures.\n")
            first_attachment = workspace / "a" / "figure.png"
            second_attachment = workspace / "b" / "figure.png"
            first_attachment.parent.mkdir()
            second_attachment.parent.mkdir()
            first_bytes = _png_bytes() + b"first"
            second_bytes = _png_bytes() + b"second"
            first_attachment.write_bytes(first_bytes)
            second_attachment.write_bytes(second_bytes)

            result = ingest_artifact(config, path=source, attachments=[first_attachment, second_attachment])
            self.assertEqual(result.figure_count, 2)

            db = connect_db(config.paths.db_path)
            try:
                rows = db.execute(
                    """
                    SELECT attachment_path, sha256
                    FROM artifact_figures
                    WHERE archive_id = ? AND storage_policy = 'source_attachment'
                    ORDER BY source_index
                    """,
                    (result.archive_id,),
                ).fetchall()
                self.assertEqual(len(rows), 2)
                paths = [Path(str(row["attachment_path"])) for row in rows]
                self.assertNotEqual(paths[0], paths[1])
                self.assertEqual(paths[0].name, "figure.png")
                self.assertEqual(paths[1].name, "figure__2.png")
                self.assertEqual(paths[0].read_bytes(), first_bytes)
                self.assertEqual(paths[1].read_bytes(), second_bytes)
                self.assertEqual(rows[0]["sha256"], sha256(first_bytes).hexdigest())
                self.assertEqual(rows[1]["sha256"], sha256(second_bytes).hexdigest())
            finally:
                db.close()

    def test_reprocess_updates_same_artifact_after_manual_raw_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = load_config(_config(workspace), workspace)
            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            connection.close()

            source = workspace / "empty.txt"
            source.write_text("")
            first = ingest_artifact(config, path=source)
            self.assertEqual(first.ingest_status, "failed_extraction")

            Path(first.raw_path).write_text("Manual projection repair made this usable.\n")
            second = reprocess_artifact(config, archive_id=first.archive_id)
            self.assertEqual(second.archive_id, first.archive_id)
            self.assertEqual(second.raw_path, first.raw_path)
            self.assertEqual(second.ingest_status, "accepted")
            self.assertFalse(second.needs_manual_projection)

            manifest = json.loads(Path(second.manifest_path).read_text())
            self.assertEqual(manifest["archive_id"], first.archive_id)
            self.assertEqual(manifest["ingest_status"], "accepted")
            self.assertIn("reprocessed_at", manifest)

            db = connect_db(config.paths.db_path)
            try:
                archive_count = db.execute("SELECT COUNT(*) AS n FROM archive WHERE id LIKE 'artifact/%'").fetchone()
                self.assertEqual(int(archive_count["n"]), 1)
                row = db.execute("SELECT ingest_status, chunk_count FROM archive WHERE id = ?", (first.archive_id,)).fetchone()
                self.assertEqual(row["ingest_status"], "accepted")
                self.assertEqual(int(row["chunk_count"]), 0)
                chunks = db.execute("SELECT COUNT(*) AS n FROM archive_chunks WHERE archive_id = ?", (first.archive_id,)).fetchone()
                self.assertEqual(int(chunks["n"]), 0)
            finally:
                db.close()

    def test_reprocess_requeues_artifact_when_raw_bytes_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = load_config(_config(workspace), workspace)
            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            connection.close()

            source = workspace / "repair.txt"
            source.write_text("Original artifact text.\n")
            first = ingest_artifact(config, path=source)

            db = connect_db(config.paths.db_path)
            try:
                with db:
                    db.execute(
                        "UPDATE extraction_queue SET status = 'processed' WHERE archive_id = ?",
                        (first.archive_id,),
                    )
                before = db.execute(
                    "SELECT status, content_hash FROM extraction_queue WHERE archive_id = ?",
                    (first.archive_id,),
                ).fetchone()
                self.assertEqual(before["status"], "processed")
                old_hash = str(before["content_hash"])
            finally:
                db.close()

            repaired_bytes = b"Manual projection repair made this usable and new.\n"
            Path(first.raw_path).write_bytes(repaired_bytes)
            reprocessed = reprocess_artifact(config, archive_id=first.archive_id)
            self.assertEqual(reprocessed.ingest_status, "accepted")

            db = connect_db(config.paths.db_path)
            try:
                after = db.execute(
                    "SELECT status, content_hash FROM extraction_queue WHERE archive_id = ?",
                    (first.archive_id,),
                ).fetchone()
                self.assertEqual(after["status"], "pending")
                self.assertEqual(after["content_hash"], sha256(repaired_bytes).hexdigest())
                self.assertNotEqual(after["content_hash"], old_hash)
            finally:
                db.close()

    def test_reprocess_failed_artifacts_retries_only_failed_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = load_config(_config(workspace), workspace)
            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            connection.close()

            failed_source = workspace / "failed.txt"
            failed_source.write_text("")
            ok_source = workspace / "ok.txt"
            ok_source.write_text("Already usable.\n")
            failed = ingest_artifact(config, path=failed_source)
            ok = ingest_artifact(config, path=ok_source)
            Path(failed.raw_path).write_text("Now usable after repair.\n")

            results = reprocess_failed_artifacts(config)
            self.assertEqual([result.archive_id for result in results], [failed.archive_id])
            self.assertEqual(results[0].ingest_status, "accepted")

            db = connect_db(config.paths.db_path)
            try:
                ok_row = db.execute("SELECT ingest_status FROM archive WHERE id = ?", (ok.archive_id,)).fetchone()
                failed_row = db.execute("SELECT ingest_status FROM archive WHERE id = ?", (failed.archive_id,)).fetchone()
                self.assertEqual(ok_row["ingest_status"], "accepted")
                self.assertEqual(failed_row["ingest_status"], "accepted")
            finally:
                db.close()

    def test_exact_duplicate_records_submission_without_duplicate_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = load_config(_config(workspace), workspace)
            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            connection.close()

            source = workspace / "sample.txt"
            source.write_text("Same artifact bytes.\n")
            first = ingest_artifact(config, path=source)
            second = ingest_artifact(config, path=source)

            self.assertEqual(second.duplicate_state, "exact_duplicate")
            self.assertEqual(second.canonical_archive_id, first.archive_id)
            self.assertEqual(second.raw_path, first.raw_path)
            manifest = json.loads(Path(second.manifest_path).read_text())
            self.assertTrue(manifest["pre_parse_short_circuited"])
            self.assertFalse((Path(second.manifest_path).parent / "original.txt").exists())

            db = connect_db(config.paths.db_path)
            try:
                rows = db.execute(
                    """
                    SELECT id, raw_sha256, duplicate_state, canonical_archive_id
                    FROM archive
                    WHERE id IN (?, ?)
                    ORDER BY id
                    """,
                    (first.archive_id, second.archive_id),
                ).fetchall()
                by_id = {row["id"]: row for row in rows}
                self.assertEqual(by_id[second.archive_id]["duplicate_state"], "exact_duplicate")
                self.assertIsNone(by_id[second.archive_id]["raw_sha256"])
                self.assertEqual(by_id[second.archive_id]["canonical_archive_id"], first.archive_id)
                bucket_count = db.execute(
                    "SELECT COUNT(*) AS n FROM artifact_identity_buckets WHERE archive_id = ?",
                    (second.archive_id,),
                ).fetchone()
                self.assertEqual(int(bucket_count["n"]), 0)
            finally:
                db.close()

    def test_canonical_duplicate_labels_but_still_keeps_projection_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = load_config(_config(workspace), workspace)
            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            connection.close()

            first_source = workspace / "sample.md"
            second_source = workspace / "sample.txt"
            first_source.write_text("Canonical identity has the same normalized words!\n")
            second_source.write_text("canonical identity has the same normalized words\n")
            first = ingest_artifact(config, path=first_source)
            second = ingest_artifact(config, path=second_source)

            self.assertEqual(second.duplicate_state, "canonical_duplicate")
            self.assertEqual(second.canonical_archive_id, first.archive_id)
            self.assertNotEqual(second.raw_path, first.raw_path)
            self.assertTrue(Path(second.raw_path).exists())

            db = connect_db(config.paths.db_path)
            try:
                row = db.execute(
                    "SELECT raw_sha256, identity_fingerprint_version FROM archive WHERE id = ?",
                    (second.archive_id,),
                ).fetchone()
                self.assertTrue(row["raw_sha256"])
                self.assertEqual(row["identity_fingerprint_version"], "text_norm_v1")
                bucket_count = db.execute(
                    "SELECT COUNT(*) AS n FROM artifact_identity_buckets WHERE archive_id = ?",
                    (second.archive_id,),
                ).fetchone()
                self.assertEqual(int(bucket_count["n"]), 0)
            finally:
                db.close()

    def test_unique_possible_and_overlap_rows_write_identity_buckets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = load_config(_config(workspace), workspace)
            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            connection.close()

            first_source = workspace / "long-a.txt"
            second_source = workspace / "long-b.txt"
            first_source.write_text(("alpha beta gamma delta epsilon zeta eta theta iota kappa " * 8).strip() + "\n")
            second_source.write_text((("alpha beta gamma delta epsilon zeta eta theta iota kappa " * 7) + "new material").strip() + "\n")
            first = ingest_artifact(config, path=first_source)
            second = ingest_artifact(config, path=second_source)

            self.assertEqual(first.duplicate_state, "unique")
            self.assertIn(second.duplicate_state, {"possible_duplicate", "overlap_related"})
            db = connect_db(config.paths.db_path)
            try:
                for archive_id in (first.archive_id, second.archive_id):
                    bucket_count = db.execute(
                        "SELECT COUNT(*) AS n FROM artifact_identity_buckets WHERE archive_id = ?",
                        (archive_id,),
                    ).fetchone()
                    self.assertEqual(int(bucket_count["n"]), 4)
            finally:
                db.close()

    def test_reprocess_duplicate_redirects_to_canonical_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = load_config(_config(workspace), workspace)
            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            connection.close()

            source = workspace / "sample.txt"
            source.write_text("Duplicate reprocess should redirect.\n")
            first = ingest_artifact(config, path=source)
            second = ingest_artifact(config, path=source)
            result = reprocess_artifact(config, archive_id=second.archive_id)

            self.assertEqual(result.archive_id, second.archive_id)
            self.assertEqual(result.raw_path, first.raw_path)
            self.assertEqual(result.duplicate_state, "exact_duplicate")
            self.assertEqual(result.canonical_archive_id, first.archive_id)

    def test_projection_exact_duplicate_returns_thin_redirect_lease(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = load_config(_config(workspace), workspace)
            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            connection.close()

            source = workspace / "sample.txt"
            source.write_text("Projection duplicate source.\n")
            first = ingest_artifact(config, path=source)
            second = ingest_artifact(config, path=source)
            service = ProjectionService(config)
            lease = service.open_projection(SourceRef("artifact", archive_id=second.archive_id))

            self.assertEqual(lease.duplicate_state, "exact_duplicate")
            self.assertEqual(lease.canonical_source_ref.archive_id, first.archive_id)
            self.assertTrue(lease.projection_metadata["pre_parse_short_circuited"])
            self.assertEqual(list(service.iter_text_windows(lease.lease_id)), [])
            self.assertEqual(list(service.iter_artifacts(lease.lease_id)), [])

    def test_projection_non_exact_duplicate_yields_text_windows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = load_config(_config(workspace), workspace)
            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            connection.close()

            first_source = workspace / "sample.md"
            second_source = workspace / "sample.txt"
            first_source.write_text("Projection canonical duplicate text!\n")
            second_source.write_text("projection canonical duplicate text\n")
            first = ingest_artifact(config, path=first_source)
            second = ingest_artifact(config, path=second_source)
            service = ProjectionService(config)
            lease = service.open_projection(SourceRef("artifact", archive_id=second.archive_id))
            windows = list(service.iter_text_windows(lease.lease_id))

            self.assertEqual(lease.duplicate_state, "canonical_duplicate")
            self.assertEqual(lease.canonical_source_ref.archive_id, first.archive_id)
            self.assertEqual(len(windows), 1)
            self.assertIn("projection canonical duplicate text", windows[0].text.casefold())

    def test_marker_grammar_round_trips_artifact_markers(self) -> None:
        marker = format_marker("fig_003", "figure", source="page:3/image:2", needs_visual_pass=True)
        parsed = parse_markers(f"Before {marker} after")

        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].artifact_id, "fig_003")
        self.assertEqual(parsed[0].kind, "figure")
        self.assertEqual(parsed[0].attrs["source"], "page:3/image:2")
        self.assertEqual(parsed[0].attrs["needs_visual_pass"], "true")

    def test_projection_artifact_lane_links_sidecar_and_honors_payload_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config = load_config(_config(workspace), workspace)
            connection = connect_db(config.paths.db_path)
            run_migrations(connection, ROOT / "migrations")
            connection.close()

            source = workspace / "paper.md"
            source.write_text("# Paper\n\nSee Figure 1.\n")
            attachment = workspace / "figure1.png"
            attachment.write_bytes(_png_bytes())
            result = ingest_artifact(config, path=source, attachments=[attachment])
            service = ProjectionService(config)
            lease = service.open_projection(
                SourceRef("artifact", archive_id=result.archive_id),
                options=ProjectionOptions(max_per_figure_bytes=4),
            )
            artifacts = list(service.iter_artifacts(lease.lease_id))

            self.assertEqual(len(artifacts), 1)
            self.assertEqual(artifacts[0].source_attachment_path, str((Path(result.raw_path).parent / "attachments" / "figure1.png").resolve()))
            self.assertIsNone(artifacts[0].payload)
            self.assertIn("payload_over_per_artifact_cap", artifacts[0].warnings)
            self.assertEqual(artifacts[0].context_windows, ["tw_000"])


if __name__ == "__main__":
    unittest.main()
