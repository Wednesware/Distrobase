import base64
import hashlib
import hmac
import secrets
import time
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


class User(BaseModel):
    username: str
    password_hash: str
    created_by_ip: str | None = None


class Registry:
    def __init__(self, db, bucket=None):
        self.db = db
        self.bucket = bucket
        self._schema_checked = False

    async def ensureSchema(self) -> None:
        if self._schema_checked:
            return

        schema_statements = [
            """
            CREATE TABLE IF NOT EXISTS users (
                username TEXT NOT NULL PRIMARY KEY,
                password_hash TEXT NOT NULL,
                created_by_ip TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS user_creation_events (
                username TEXT NOT NULL,
                ip TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (username, ip, created_at)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS distributions (
                author TEXT NOT NULL,
                name TEXT NOT NULL,
                latest TEXT,
                PRIMARY KEY (author, name)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS releases (
                author TEXT NOT NULL,
                distribution TEXT NOT NULL,
                version TEXT NOT NULL,
                PRIMARY KEY (author, distribution, version)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS artifacts (
                author TEXT NOT NULL,
                distribution TEXT NOT NULL,
                version TEXT NOT NULL,
                name TEXT NOT NULL,
                size INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                r2_key TEXT NOT NULL,
                PRIMARY KEY (author, distribution, version, name)
            )
            """,
        ]

        for statement in schema_statements:
            await self.db.prepare(statement).run()

        self._schema_checked = True

    @staticmethod
    def _hash_password(password: str) -> str:
        salt = secrets.token_bytes(16)
        iterations = 200_000
        derived = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            iterations,
        )
        salt_b64 = base64.b64encode(salt).decode("ascii").rstrip("=")
        digest_b64 = base64.b64encode(derived).decode("ascii").rstrip("=")
        return f"pbkdf2_sha256${iterations}${salt_b64}${digest_b64}"

    @staticmethod
    def _verify_password(password: str, stored_hash: str | None) -> bool:
        if not stored_hash:
            return False

        if stored_hash.startswith("pbkdf2_sha256$"):
            try:
                _, iterations_raw, salt_b64, digest_b64 = stored_hash.split("$", 3)
                iterations = int(iterations_raw)
                salt = base64.b64decode(salt_b64 + "=" * (-len(salt_b64) % 4))
                expected = base64.b64decode(digest_b64 + "=" * (-len(digest_b64) % 4))
                actual = hashlib.pbkdf2_hmac(
                    "sha256",
                    password.encode("utf-8"),
                    salt,
                    iterations,
                )
                return hmac.compare_digest(actual, expected)
            except (TypeError, ValueError):
                return False

        legacy_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
        return hmac.compare_digest(legacy_hash, stored_hash)

    async def createUser(
        self,
        username: str,
        password: str,
        ip_address: str | None = None,
        max_creations_per_ip: int = 5,
        window_seconds: int = 3600,
    ) -> User:
        await self.ensureSchema()
        if not username or not username.strip():
            raise RegistryError("Username is required")
        if not password or not password.strip():
            raise RegistryError("Password is required")

        username = username.strip()
        ip_address = ip_address or "unknown"

        cutoff = int(time.time()) - window_seconds
        creation_count_result = await self.db.prepare(
            """
            SELECT COUNT(*) AS total
            FROM user_creation_events
            WHERE ip = ? AND created_at >= ?
            """
        ).bind(
            ip_address,
            cutoff,
        ).first()
        creation_count = int((creation_count_result or {}).get("total", 0))
        if creation_count >= max_creations_per_ip:
            raise RegistryError("Too many user creations from this IP; try again later.")

        existing_user = await self.db.prepare(
            """
            SELECT username
            FROM users
            WHERE username = ?
            """
        ).bind(
            username,
        ).first()

        if existing_user is not None:
            raise RegistryError(f"User {username} already exists")

        await self.db.prepare(
            """
            INSERT INTO users (username, password_hash, created_by_ip)
            VALUES (?, ?, ?)
            """
        ).bind(
            username,
            self._hash_password(password),
            ip_address,
        ).run()

        await self.db.prepare(
            """
            INSERT INTO user_creation_events (username, ip, created_at)
            VALUES (?, ?, ?)
            """
        ).bind(
            username,
            ip_address,
            int(time.time()),
        ).run()

        return User(
            username=username,
            password_hash=self._hash_password(password),
            created_by_ip=ip_address,
        )

    async def getUser(self, username: str) -> User:
        await self.ensureSchema()
        if not username or not username.strip():
            raise RegistryError("Username is required")

        user_result = await self.db.prepare(
            """
            SELECT username, password_hash, created_by_ip
            FROM users
            WHERE username = ?
            """
        ).bind(
            username.strip(),
        ).first()

        if user_result is None:
            raise RegistryError(f"User {username} not found")

        return User(
            username=user_result["username"],
            password_hash=user_result["password_hash"],
            created_by_ip=user_result.get("created_by_ip"),
        )

    async def listUsers(self) -> list[User]:
        await self.ensureSchema()
        users_result = await self.db.prepare(
            """
            SELECT username, password_hash, created_by_ip
            FROM users
            ORDER BY username ASC
            """
        ).all()

        return [
            User(
                username=user["username"],
                password_hash=user["password_hash"],
                created_by_ip=user.get("created_by_ip"),
            )
            for user in users_result.results
        ]

    async def requireUserPassword(
        self,
        username: str,
        password: str | None,
        ip_address: str | None = None,
    ) -> User:
        await self.ensureSchema()
        if not username or not username.strip():
            raise RegistryError("Authentication required")
        if not password:
            raise RegistryError("Authentication required")

        user_result = await self.db.prepare(
            """
            SELECT username, password_hash, created_by_ip
            FROM users
            WHERE username = ?
            """
        ).bind(
            username,
        ).first()

        if user_result is None:
            raise RegistryError("Authentication required")

        if not self._verify_password(password, user_result["password_hash"]):
            raise RegistryError("Authentication required")

        if not user_result["password_hash"].startswith("pbkdf2_sha256$"):
            await self.db.prepare(
                """
                UPDATE users
                SET password_hash = ?
                WHERE username = ?
                """
            ).bind(
                self._hash_password(password),
                username,
            ).run()

        return User(
            username=user_result["username"],
            password_hash=user_result["password_hash"],
            created_by_ip=user_result.get("created_by_ip"),
        )

    async def getDistribution(
        self,
        author: str,
        distribution: str,
    ) -> Distribution:
        await self.ensureSchema()
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
        await self.ensureSchema()
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
        password: str | None = None,
        ip_address: str | None = None,
    ) -> Artifact:
        await self.ensureSchema()
        await self.requireUserPassword(author, password, ip_address)

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

    async def deleteRelease(
        self,
        author: str,
        distribution: str,
        release: str,
        password: str | None = None,
        ip_address: str | None = None,
    ) -> dict:
        await self.ensureSchema()
        await self.requireUserPassword(author, password, ip_address)

        artifact_rows = await self.db.prepare(
            """
            SELECT name, r2_key
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

        for artifact in artifact_rows.results:
            if self.bucket is not None and hasattr(self.bucket, "delete"):
                await self.bucket.delete(artifact["r2_key"])

        await self.db.prepare(
            """
            DELETE FROM artifacts
            WHERE author = ?
              AND distribution = ?
              AND version = ?
            """
        ).bind(
            author,
            distribution,
            release,
        ).run()

        await self.db.prepare(
            """
            DELETE FROM releases
            WHERE author = ?
              AND distribution = ?
              AND version = ?
            """
        ).bind(
            author,
            distribution,
            release,
        ).run()

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

        if distribution_row is not None and distribution_row["latest"] == release:
            await self.db.prepare(
                """
                UPDATE distributions
                SET latest = NULL
                WHERE author = ? AND name = ?
                """
            ).bind(
                author,
                distribution,
            ).run()

        return {
            "author": author,
            "distribution": distribution,
            "release": release,
            "deleted": True,
        }

    async def getArtifact(
        self,
        author: str,
        distribution: str,
        release: str,
        artifact: str,
    ):
        await self.ensureSchema()
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