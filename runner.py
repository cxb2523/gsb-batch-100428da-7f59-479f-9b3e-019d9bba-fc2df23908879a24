import time
import traceback

from task import main

sleeptime = 300


def run_once(engine="rpabrowser_js"):
    """Run one collection round, swallowing failures so the loop can continue."""
    try:
        return main(engine)
    except Exception:
        # A failed round must never end the collection loop; log it and keep
        # going.  Record handling already reports page errors per engine, so
        # anything surfacing here is an unexpected failure (e.g. Excel open).
        print("Run failed; continuing with the next round")
        traceback.print_exc()
        return None


def sleep_between_rounds(seconds=sleeptime):
    try:
        from progress.bar import Bar
    except ImportError:  # pragma: no cover - depends on the environment
        time.sleep(seconds)
        return
    bar = Bar("sleeping", max=seconds)
    for _ in range(seconds):
        bar.next()
        time.sleep(1)
    bar.finish()


def main_forever(engine="rpabrowser_js"):
    while True:
        run_once(engine)
        sleep_between_rounds()


if __name__ == "__main__":
    main_forever()
