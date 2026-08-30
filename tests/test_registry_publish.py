import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timezone

import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from registry import Registry, RegistryError


class FakeResult:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class FakeStatement:
    def __init__(self, db, sql):
        self.db = db
        self.sql = sql
        self.bind_values = []

    def bind(self, *values):
        self.bind_values = list(values)
        return self

    async def first(self):
        table = self.db.data
        sql = self.sql.strip()
        if "SELECT COUNT(*) AS total" in sql:
            ip = self.bind_values[0]
            cutoff = self.bind_values[1]
            total = 0
            for row in table["user_creation_events"]:
                if row["ip"] == ip and row["created_at"] >= cutoff:
                    total += 1
            return {"total": total}
        if "SELECT username" in sql and "FROM users" in sql:
            username = self.bind_values[0]
            for row in table["users"]:
                if row["username"] == username:
                    return row
            return None
        if "SELECT author, name, latest" in sql:
            for row in table["distributions"]:
                if row["author"] == self.bind_values[0] and row["name"] == self.bind_values[1]:
                    return row
            return None
        if "SELECT author, distribution, version" in sql:
            for row in table["releases"]:
                if row["author"] == self.bind_values[0] and row["distribution"] == self.bind_values[1] and row["version"] == self.bind_values[2]:
                    return row
            return None
        if "SELECT name, size, sha256, r2_key" in sql:
            for row in table["artifacts"]:
                if row["author"] == self.bind_values[0] and row["distribution"] == self.bind_values[1] and row["version"] == self.bind_values[2] and row["name"] == self.bind_values[3]:
                    return row
            return None
        return None

    async def all(self):
        sql = self.sql.strip()
        if "SELECT name, size, sha256" in sql:
            rows = []
            for row in self.db.data["artifacts"]:
                if row["author"] == self.bind_values[0] and row["distribution"] == self.bind_values[1] and row["version"] == self.bind_values[2]:
                    rows.append({
                        "name": row["name"],
                        "size": row["size"],
                        "sha256": row["sha256"],
                    })
            return FakeResult(results=rows)
        if "SELECT username, password_hash, created_at" in sql:
            rows = []
            for row in self.db.data["users"]:
                rows.append({
                    "username": row["username"],
                    "password_hash": row["password_hash"],
                    "created_at": row["created_at"],
                })
            return FakeResult(results=rows)
        return FakeResult(results=[])

    async def run(self):
        sql = self.sql.strip()

        if sql.startswith("CREATE TABLE IF NOT EXISTS"):
            self.db.data.setdefault("schema", []).append(sql)
            return FakeResult()

        if sql.startswith("INSERT INTO users"):
            self.db.data["users"].append({
                "username": self.bind_values[0],
                "password_hash": self.bind_values[1],
                "created_at": self.bind_values[2],
            })
            return FakeResult()

        if sql.startswith("INSERT INTO user_creation_events"):
            self.db.data["user_creation_events"].append({
                "username": self.bind_values[0],
                "ip": self.bind_values[1],
                "created_at": self.bind_values[2],
            })
            return FakeResult()

        if sql.startswith("INSERT INTO distributions"):
            self.db.data["distributions"].append({
                "author": self.bind_values[0],
                "name": self.bind_values[1],
                "latest": self.bind_values[2],
            })
            return FakeResult()

        if sql.startswith("INSERT INTO releases"):
            self.db.data["releases"].append({
                "author": self.bind_values[0],
                "distribution": self.bind_values[1],
                "version": self.bind_values[2],
            })
            return FakeResult()

        if sql.startswith("INSERT INTO artifacts"):
            self.db.data["artifacts"].append({
                "author": self.bind_values[0],
                "distribution": self.bind_values[1],
                "version": self.bind_values[2],
                "name": self.bind_values[3],
                "size": self.bind_values[4],
                "sha256": self.bind_values[5],
                "r2_key": self.bind_values[6],
            })
            return FakeResult()

        if sql.startswith("UPDATE distributions"):
            for row in self.db.data["distributions"]:
                if row["author"] == self.bind_values[1] and row["name"] == self.bind_values[2]:
                    row["latest"] = self.bind_values[0]
                    break
            return FakeResult()

        raise AssertionError(f"Unexpected SQL: {sql}")


class FakeDB:
    def __init__(self):
        self.data = {
            "users": [],
            "user_creation_events": [],
            "distributions": [],
            "releases": [],
            "artifacts": [],
        }

    def prepare(self, sql):
        return FakeStatement(self, sql)


class FakeBucket:
    def __init__(self):
        self.objects = {}

    async def put(self, key, content):
        self.objects[key] = content
        return {"key": key}


class FakeUpload:
    def __init__(self, filename, body):
        self.filename = filename
        self._body = body

    async def read(self):
        return self._body


def test_publish_release_stores_r2_and_d1_metadata():
    async def run_test():
        db = FakeDB()
        bucket = FakeBucket()
        registry = Registry(db, bucket)
        content = b"artifact-data"
        artifact = FakeUpload("artifact.tar.gz", content)

        await registry.createUser("alkli", "hunter2")
        result = await registry.publishRelease("alkli", "myproject", "26.5", artifact, password="hunter2")

        assert result.name == "artifact.tar.gz"
        assert result.size == len(content)
        assert result.sha256 == hashlib.sha256(content).hexdigest()
        assert bucket.objects["alkli/myproject/26.5/artifact.tar.gz"] == content
        assert db.data["distributions"][0]["latest"] == "26.5"
        assert db.data["releases"][0]["version"] == "26.5"
        assert db.data["artifacts"][0]["sha256"] == result.sha256

    asyncio.run(run_test())


def test_publish_requires_valid_user_password_and_ip_limit():
    async def run_test():
        db = FakeDB()
        bucket = FakeBucket()
        registry = Registry(db, bucket)
        content = b"artifact-data"
        artifact = FakeUpload("artifact.tar.gz", content)

        await registry.createUser("alkli", "hunter2")

        with pytest.raises(RegistryError, match="Authentication required"):
            await registry.publishRelease("alkli", "myproject", "26.6", artifact, password="wrong")

        for index in range(6):
            try:
                await registry.createUser(f"user{index}", "pw")
            except RegistryError:
                pass

        with pytest.raises(RegistryError, match="Too many user creations"):
            await registry.createUser("user-too-many", "pw")

    asyncio.run(run_test())


def test_get_and_list_users():
    async def run_test():
        db = FakeDB()
        registry = Registry(db)

        await registry.createUser("alkli", "hunter2")
        await registry.createUser("torch", "secret")

        user = await registry.getUser("alkli")
        assert user.username == "alkli"
        assert user.created_at is not None

        users = await registry.listUsers()
        assert [entry.username for entry in users] == ["alkli", "torch"]

    asyncio.run(run_test())


def test_password_hash_is_salted_and_not_plain_sha256():
    async def run_test():
        db = FakeDB()
        registry = Registry(db)

        await registry.createUser("alkli", "hunter2")

        stored_hash = (await registry.getUser("alkli")).password_hash
        assert stored_hash != hashlib.sha256("hunter2".encode("utf-8")).hexdigest()
        assert stored_hash.startswith("pbkdf2_sha256$")

        auth_user = await registry.requireUserPassword("alkli", "hunter2")
        assert auth_user.username == "alkli"

        with pytest.raises(RegistryError, match="Authentication required"):
            await registry.requireUserPassword("alkli", "wrong-password")

    asyncio.run(run_test())


def test_user_routes_precede_distribution_routes():
    import importlib
    import types

    workers_module = types.ModuleType("workers")
    workers_module.WorkerEntrypoint = object
    workers_module.Response = object
    sys.modules["workers"] = workers_module

    asgi_module = types.ModuleType("asgi")
    asgi_module.fetch = lambda *args, **kwargs: None
    sys.modules["asgi"] = asgi_module

    import entry
    importlib.reload(entry)

    paths = [route.path for route in entry.app.routes if hasattr(route, "path")]
    assert paths.index("/v1/users") < paths.index("/v1/{author}/{distribution}")
    assert paths.index("/v1/users/{username}") < paths.index("/v1/{author}/{distribution}")


def test_ensure_schema_creates_missing_tables():
    async def run_test():
        db = FakeDB()
        registry = Registry(db)

        await registry.ensureSchema()

        assert any("CREATE TABLE IF NOT EXISTS users" in statement for statement in db.data["schema"])
        assert any("CREATE TABLE IF NOT EXISTS user_creation_events" in statement for statement in db.data["schema"])
        assert any("CREATE TABLE IF NOT EXISTS distributions" in statement for statement in db.data["schema"])

    asyncio.run(run_test())


if __name__ == "__main__":
    test_publish_release_stores_r2_and_d1_metadata()
    test_publish_requires_valid_user_password_and_ip_limit()
    test_get_and_list_users()
    print("ok")
