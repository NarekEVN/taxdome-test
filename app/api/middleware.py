import os
import socket

from fastapi import FastAPI, Request


def add_instance_header(app: FastAPI) -> None:
    """Tag responses with the container hostname and worker pid, so load balancing is visible."""
    instance = f"{socket.gethostname()}:{os.getpid()}"

    @app.middleware("http")
    async def _instance_header(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Instance"] = instance
        return response
