import base64
from urllib.parse import urlparse
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
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

# Reading the registry is public, so any site may read it from a browser (for example a package
# index). Only GET is allowed cross-origin and no credentials are accepted, so publishing,
# deleting and account routes stay usable only by non-browser clients such as Hydrogen and Pulley.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_credentials=False,
)
PUBLIC_READ_HEADERS = {"Access-Control-Allow-Origin": "*"}

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

def get_basic_auth_credentials(request: Request):
    header = request.headers.get("authorization")
    if not header or not header.lower().startswith("basic "):
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
        )

    try:
        encoded = header.split(" ", 1)[1]
        decoded = base64.b64decode(encoded).decode("utf-8")
        username, password = decoded.split(":", 1)
        return username.strip().lower(), password
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
        ) from exc


@app.get("/v1/users")
async def list_users(request: Request):
    env = get_worker_env(request)
    registry = Registry(
        env.distrobase_db,
        env.distrobase_artifacts,
    )

    users = await registry.listUsers()
    return [
        {
            "username": user.username,
            "createdAt": user.created_at,
        }
        for user in users
    ]


@app.get("/v1/distributions")
async def list_distributions(request: Request, author: str | None = None):
    env = get_worker_env(request)
    registry = Registry(
        env.distrobase_db,
        env.distrobase_artifacts,
    )

    return await registry.listDistributions(author)


@app.get("/v1/users/{username}")
async def get_user(username: str, request: Request):
    env = get_worker_env(request)
    registry = Registry(
        env.distrobase_db,
        env.distrobase_artifacts,
    )

    try:
        auth_username, password = get_basic_auth_credentials(request)
    except HTTPException:
        raise

    if auth_username.lower() != username.lower():
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
        )

    try:
        await registry.requireUserPassword(username, password)
        user = await registry.getUser(username)
        return {
            "username": user.username,
            "createdAt": user.created_at,
        }
    except RegistryError as error:
        if str(error) == "Authentication required":
            raise HTTPException(
                status_code=401,
                detail="Authentication required",
            )
        raise HTTPException(
            status_code=404,
            detail=str(error),
        )


@app.post("/v1/users")
async def create_user(request: Request):
    env = get_worker_env(request)
    registry = Registry(
        env.distrobase_db,
        env.distrobase_artifacts,
    )

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Request body must be JSON",
        )

    username = payload.get("username")
    password = payload.get("password")
    if not username or not password:
        raise HTTPException(
            status_code=400,
            detail="username and password are required",
        )

    try:
        user = await registry.createUser(
            str(username),
            str(password),
        )
        return {
            "username": user.username,
            "createdAt": user.created_at,
        }
    except RegistryError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        )


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
        found = await registry.getDistribution(
            author,
            distribution,
        )
        found.releases = await registry.listReleases(author, distribution)
        return found
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
        username, password = get_basic_auth_credentials(request)
        if username.lower() != author.lower():
            raise HTTPException(
                status_code=401,
                detail="Authentication required",
            )
        return await registry.publishRelease(
            author,
            distribution,
            release,
            artifact,
            password=password,
        )
    except HTTPException:
        raise
    except RegistryError as error:
        raise HTTPException(
            status_code=401 if "Authentication required" in str(error) else 409,
            detail=str(error),
        )


@app.delete("/v1/{author}/{distribution}/{release}")
async def delete_release(
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
        username, password = get_basic_auth_credentials(request)
        if username.lower() != author.lower():
            raise HTTPException(
                status_code=401,
                detail="Authentication required",
            )
        return await registry.deleteRelease(
            author,
            distribution,
            release,
            password=password,
        )
    except HTTPException:
        raise
    except RegistryError as error:
        raise HTTPException(
            status_code=401 if "Authentication required" in str(error) else 409,
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
                        **PUBLIC_READ_HEADERS,
                    },
                )

            except ArtifactNotFound:
                return Response(
                    "Artifact not found",
                    status=404,
                    headers=PUBLIC_READ_HEADERS,
                )

        return await asgi.fetch(
            app,
            request,
            self.env,
        )