from __future__ import annotations

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
from second_brain.ingest import ingest_artifact
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


if __name__ == "__main__":
    unittest.main()
