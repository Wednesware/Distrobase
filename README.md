## Usage

You can run the Worker defined by your new project by executing `wrangler dev` in this
directory. This will start up an HTTP server and will allow you to iterate on your
Worker without having to restart `wrangler`.

### Secure publishing

Artifact uploads are intentionally fail-closed: the service requires a bearer token to
publish new releases. Configure the token in your Worker environment as
`DISTROBASE_AUTH_TOKEN` (or `distrobase_auth_token`) before enabling uploads. The
service also rejects unsafe filenames, empty uploads, and files larger than 100 MiB.

### Types and autocomplete

This project also includes a pyproject.toml with some requirements which
set up autocomplete and type hints for this Python Workers project.

To get these installed you'll need `uv`, which you can install by following
https://docs.astral.sh/uv/getting-started/installation/.

Once `uv` is installed, you can run the following:

```
uv venv
uv sync
```

Then point your editor's Python plugin at the `.venv` directory. You should then have working
autocomplete and type information in your editor.
