"""Production entry point — serves the app via waitress (Windows-compatible WSGI server)."""
import os
from waitress import serve
from app import create_app

app = create_app()

if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "5000"))
    threads = int(os.getenv("THREADS", "4"))

    print(f"Starting production server on http://{host}:{port} ({threads} threads)")
    serve(app, host=host, port=port, threads=threads)
