from urllib.parse import urlparse
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from workers import WorkerEntrypoint, Response  # type: ignore
import asgi  # type: ignore

from registry import (
    Artifact,
    Registry,
    DistributionNotFound,
    ReleaseNotFound,
    ArtifactNotFound,
    RegistryError,
)

app = FastAPI()


def get_worker_env(request: Request):
    env = getattr(request.state, "env", None)
    if env is not None:
        return env

    env = request.scope.get("env")
    if env is not None:
        request.state.env = env
        return env

    raise RuntimeError("Worker environment is not configured for this request")


@app.get("/")
async def root():
    return {
        "service": "distrobase",
        "status": "ok",
    }


@app.get("/v1/{author}/{distribution}")
async def get_distribution(
    author: str,
    distribution: str,
    request: Request,
):
    env = get_worker_env(request)
    registry = Registry(
        env.distrobase_db,
        env.distrobase_artifacts,
    )

    try:
        return await registry.getDistribution(
            author,
            distribution,
        )
    except DistributionNotFound:
        raise HTTPException(
            status_code=404,
            detail="Distribution not found",
        )


@app.get("/v1/{author}/{distribution}/{release}")
async def get_release(
    author: str,
    distribution: str,
    release: str,
    request: Request,
):
    env = get_worker_env(request)
    registry = Registry(
        env.distrobase_db,
        env.distrobase_artifacts,
    )

    try:
        return await registry.getRelease(
            author,
            distribution,
            release,
        )
    except ReleaseNotFound:
        raise HTTPException(
            status_code=404,
            detail="Release not found",
        )


@app.post("/v1/{author}/{distribution}/{release}")
async def publish_release(
    author: str,
    distribution: str,
    release: str,
    artifact: UploadFile = File(...),
    request: Request = None,
):
    env = get_worker_env(request)
    registry = Registry(
        env.distrobase_db,
        env.distrobase_artifacts,
    )

    try:
        return await registry.publishRelease(
            author,
            distribution,
            release,
            artifact,
        )
    except RegistryError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        )


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        url = urlparse(request.url)
        parts = url.path.strip("/").split("/")

        # /v1/{author}/{distribution}/{release}/{artifact}
        if (
            len(parts) >= 5
            and parts[0] == "v1"
        ):
            author = parts[1]
            distribution = parts[2]
            release = parts[3]
            artifact = "/".join(parts[4:])

            try:
                result = await Registry(
                    self.env.distrobase_db,
                    self.env.distrobase_artifacts,
                ).getArtifact(
                    author,
                    distribution,
                    release,
                    artifact,
                )

                obj = await self.env.distrobase_artifacts.get(
                    result["r2_key"]
                )

                if obj is None:
                    return Response(
                        "Artifact not found",
                        status=404,
                    )

                return Response(
                    obj.body,
                    status=200,
                    headers={
                        "Content-Type": "application/octet-stream",
                    },
                )

            except ArtifactNotFound:
                return Response(
                    "Artifact not found",
                    status=404,
                )

        return await asgi.fetch(
            app,
            request,
            self.env,
        )