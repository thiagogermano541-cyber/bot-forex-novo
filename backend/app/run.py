"""Bind IPv4 and IPv6 for Railway private networking and its dynamic PORT."""
import os
import socket

import uvicorn


def main():
    port = int(os.getenv("PORT", "8000"))
    if socket.has_dualstack_ipv6():
        listener = socket.create_server(("::", port), family=socket.AF_INET6, dualstack_ipv6=True)
    else:
        listener = socket.create_server(("0.0.0.0", port))
    server = uvicorn.Server(uvicorn.Config("app.main:app", proxy_headers=False))
    server.run(sockets=[listener])


if __name__ == "__main__":
    main()
