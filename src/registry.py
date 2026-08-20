import hashlib
from pathlib import Path

from pydantic import BaseModel


class RegistryError(Exception):
    pass


class DistributionNotFound(RegistryError):
    pass


class ReleaseNotFound(RegistryError):
    pass


class ArtifactNotFound(RegistryError):
    pass


class Artifact(BaseModel):
    name: str
    size: int
    sha256: str


class Distribution(BaseModel):
    author: str
    distribution: str
    latest: str


class Release(BaseModel):
    author: str
    distribution: str
    release: str
    artifacts: list[Artifact]


class Registry:
    def __init__(self, db, bucket=None):
        self.db = db
        self.bucket = bucket

    async def getDistribution(
        self,
        author: str,
        distribution: str,
    ) -> Distribution:
        result = await self.db.prepare(
            """
            SELECT author, name, latest
            FROM distributions
            WHERE author = ? AND name = ?
            """
        ).bind(
            author,
            distribution,
        ).first()

        if result is None or result["latest"] is None:
            raise DistributionNotFound()

        return Distribution(
            author=result["author"],
            distribution=result["name"],
            latest=result["latest"],
        )

    async def getRelease(
        self,
        author: str,
        distribution: str,
        release: str,
    ) -> Release:
        result = await self.db.prepare(
            """
            SELECT author, distribution, version
            FROM releases
            WHERE author = ?
              AND distribution = ?
              AND version = ?
            """
        ).bind(
            author,
            distribution,
            release,
        ).first()

        if result is None:
            raise ReleaseNotFound()

        artifacts_result = await self.db.prepare(
            """
            SELECT name, size, sha256
            FROM artifacts
            WHERE author = ?
              AND distribution = ?
              AND version = ?
            """
        ).bind(
            author,
            distribution,
            release,
        ).all()

        artifacts = [
            Artifact(
                name=artifact["name"],
                size=artifact["size"],
                sha256=artifact["sha256"],
            )
            for artifact in artifacts_result.results
        ]

        return Release(
            author=author,
            distribution=distribution,
            release=release,
            artifacts=artifacts,
        )

    async def publishRelease(
        self,
        author: str,
        distribution: str,
        release: str,
        artifact,
    ) -> Artifact:
        if self.bucket is None:
            raise RegistryError("Artifact storage is not configured")

        artifact_name = Path((artifact.filename or "")).name
        if not artifact_name:
            raise RegistryError("Artifact must have a filename")

        content = await artifact.read()
        size = len(content)
        sha256 = hashlib.sha256(content).hexdigest()
        r2_key = f"{author}/{distribution}/{release}/{artifact_name}"

        existing_release = await self.db.prepare(
            """
            SELECT author, distribution, version
            FROM releases
            WHERE author = ?
              AND distribution = ?
              AND version = ?
            """
        ).bind(
            author,
            distribution,
            release,
        ).first()

        if existing_release is not None:
            raise RegistryError(f"Release {release} already exists")

        existing_artifact = await self.db.prepare(
            """
            SELECT name, size, sha256, r2_key
            FROM artifacts
            WHERE author = ?
              AND distribution = ?
              AND version = ?
              AND name = ?
            """
        ).bind(
            author,
            distribution,
            release,
            artifact_name,
        ).first()

        if existing_artifact is not None:
            raise RegistryError(f"Artifact {artifact_name} already exists")

        try:
            await self.bucket.put(r2_key, content)

            distribution_row = await self.db.prepare(
                """
                SELECT author, name, latest
                FROM distributions
                WHERE author = ? AND name = ?
                """
            ).bind(
                author,
                distribution,
            ).first()

            if distribution_row is None:
                await self.db.prepare(
                    """
                    INSERT INTO distributions (author, name, latest)
                    VALUES (?, ?, ?)
                    """
                ).bind(
                    author,
                    distribution,
                    release,
                ).run()
            else:
                await self.db.prepare(
                    """
                    UPDATE distributions
                    SET latest = ?
                    WHERE author = ? AND name = ?
                    """
                ).bind(
                    release,
                    author,
                    distribution,
                ).run()

            await self.db.prepare(
                """
                INSERT INTO releases (author, distribution, version)
                VALUES (?, ?, ?)
                """
            ).bind(
                author,
                distribution,
                release,
            ).run()

            await self.db.prepare(
                """
                INSERT INTO artifacts (author, distribution, version, name, size, sha256, r2_key)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """
            ).bind(
                author,
                distribution,
                release,
                artifact_name,
                size,
                sha256,
                r2_key,
            ).run()

            return Artifact(
                name=artifact_name,
                size=size,
                sha256=sha256,
            )
        except Exception:
            if hasattr(self.bucket, "delete"):
                await self.bucket.delete(r2_key)
            raise

    async def getArtifact(
        self,
        author: str,
        distribution: str,
        release: str,
        artifact: str,
    ):
        result = await self.db.prepare(
            """
            SELECT name, size, sha256, r2_key
            FROM artifacts
            WHERE author = ?
              AND distribution = ?
              AND version = ?
              AND name = ?
            """
        ).bind(
            author,
            distribution,
            release,
            artifact,
        ).first()

        if result is None:
            raise ArtifactNotFound()

        return result