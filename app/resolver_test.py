from app.resolver import get_stream


if __name__ == "__main__":
    channel_id = input("Channel ID: ").strip()

    result = get_stream(channel_id)

    print("\n=== SUCCESS ===")
    print("Channel:", result["channel_id"])
    print("Backend:", result["backend_url"])
    print("Master:", result["master_m3u8"])
    print("Media:", result["media_m3u8"])
    print("\nPlaylist:\n")
    print(result["media_playlist"])
