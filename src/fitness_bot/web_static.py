"""Revalidate Playground assets so a refresh cannot mix report protocol versions."""

from fastapi.staticfiles import StaticFiles


class PlaygroundFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response
