import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace

import sys

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
        return FakeResult(results=[])

    async def run(self):
        sql = self.sql.strip()

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

        result = await registry.publishRelease("alkli", "myproject", "26.5", artifact)

        assert result.name == "artifact.tar.gz"
        assert result.size == len(content)
        assert result.sha256 == hashlib.sha256(content).hexdigest()
        assert bucket.objects["alkli/myproject/26.5/artifact.tar.gz"] == content
        assert db.data["distributions"][0]["latest"] == "26.5"
        assert db.data["releases"][0]["version"] == "26.5"
        assert db.data["artifacts"][0]["sha256"] == result.sha256

    asyncio.run(run_test())


if __name__ == "__main__":
    test_publish_release_stores_r2_and_d1_metadata()
    print("ok")
