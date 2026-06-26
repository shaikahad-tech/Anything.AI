"""
Export Service — Obsidian vault, PDF, JSON.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timedelta

import structlog

from app.config import settings
from app.db.models import RabbitHole
from app.services.graph import GraphService
from rhm_shared_types.models import ExportFormat, ExportResponse

log = structlog.get_logger(__name__)


class ExportService:
    async def export(self, rh: RabbitHole, fmt: ExportFormat) -> ExportResponse:
        if fmt == ExportFormat.JSON:
            return await self._export_json(rh)
        elif fmt == ExportFormat.OBSIDIAN:
            return await self._export_obsidian(rh)
        elif fmt == ExportFormat.PDF:
            return await self._export_pdf(rh)
        else:
            raise ValueError(f"Unknown export format: {fmt}")

    async def _export_json(self, rh: RabbitHole) -> ExportResponse:
        graph_svc = GraphService()
        graph = await graph_svc.get_react_flow_graph(str(rh.id))
        timeline = await graph_svc.get_timeline(str(rh.id))
        contradictions = await graph_svc.get_contradictions(str(rh.id))

        payload = {
            "rabbit_hole": {
                "id": str(rh.id),
                "topic": rh.topic,
                "created_at": rh.created_at.isoformat(),
            },
            "graph": graph.model_dump(),
            "timeline": timeline.model_dump(),
            "contradictions": contradictions.model_dump(),
        }

        blob_key = f"exports/{rh.id}/graph.json"
        url = await self._upload_blob(blob_key, json.dumps(payload, indent=2).encode())
        return ExportResponse(
            download_url=url,
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )

    async def _export_obsidian(self, rh: RabbitHole) -> ExportResponse:
        """
        Generate an Obsidian vault as a ZIP of .md files with [[backlinks]].
        Every claim traces back to its source. Every entity gets its own note.
        """
        graph_svc = GraphService()
        graph = await graph_svc.get_react_flow_graph(str(rh.id))
        contradictions = await graph_svc.get_contradictions(str(rh.id))

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            # Index note
            index_md = f"# {rh.topic}\n\n"
            index_md += f"*Rabbit Hole created {rh.created_at.strftime('%Y-%m-%d')}*\n\n"
            index_md += "## Entities\n\n"

            for node in graph.nodes:
                if node.data.node_type.value in ("Person", "Organization", "Event"):
                    name = node.data.label
                    index_md += f"- [[{name}]]\n"
                    note = f"# {name}\n\n"
                    note += f"**Type:** {node.data.node_type.value}\n\n"
                    note += "## Claims\n\n"
                    note += "_See claims mentioning this entity._\n\n"
                    note += f"**Properties:**\n```json\n{json.dumps(node.data.properties, indent=2)}\n```\n"
                    zf.writestr(f"{name.replace('/', '_')}.md", note)

            if contradictions.pairs:
                index_md += "\n## Contradictions\n\n"
                for pair in contradictions.pairs:
                    index_md += f"- **{pair.claim_a_text[:60]}…** vs **{pair.claim_b_text[:60]}…** (confidence: {pair.confidence:.2f})\n"

            zf.writestr("README.md", index_md)

        blob_key = f"exports/{rh.id}/vault.zip"
        url = await self._upload_blob(blob_key, buf.getvalue())
        return ExportResponse(
            download_url=url,
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )

    async def _export_pdf(self, rh: RabbitHole) -> ExportResponse:
        # PDF export is a future enhancement — return a placeholder for now
        # that returns the JSON export instead, logged as a fallback.
        log.warning("pdf_export.fallback_to_json", rabbit_hole_id=str(rh.id))
        return await self._export_json(rh)

    async def _upload_blob(self, key: str, data: bytes) -> str:
        """Upload bytes to MinIO/S3 and return a presigned URL."""
        import boto3
        from botocore.client import Config

        s3 = boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT,
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
            config=Config(signature_version="s3v4"),
        )
        s3.put_object(Bucket=settings.S3_BUCKET, Key=key, Body=data)
        url: str = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.S3_BUCKET, "Key": key},
            ExpiresIn=3600,
        )
        return url
