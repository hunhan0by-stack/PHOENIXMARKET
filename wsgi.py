from app import app

# This file is the WSGI entrypoint for production servers (e.g. gunicorn, uWSGI).
# Example: gunicorn --workers 1 --threads 4 wsgi:app
# Keep one worker: rate limits (sign-in, checkout, chat) live in process memory.

if __name__ == "__main__":
    # Allow running directly for ad‑hoc checks; uses the same env-driven debug flag.
    import os

    debug_flag = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(debug=debug_flag)

