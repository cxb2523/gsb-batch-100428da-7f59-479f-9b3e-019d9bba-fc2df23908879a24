import sys


def safe_print(message):
    """Prints a message without aborting a crawl on consoles unable to encode non-ASCII characters."""
    try:
        print(message)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        print(message.encode(encoding, "replace").decode(encoding, "replace"))
