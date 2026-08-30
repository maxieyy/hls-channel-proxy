from app.channel_gateway import app
import os


if __name__ == "__main__":
    app.run(
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("GATEWAY_PORT", "8090")),
        debug=False,
        threaded=True,
    )
