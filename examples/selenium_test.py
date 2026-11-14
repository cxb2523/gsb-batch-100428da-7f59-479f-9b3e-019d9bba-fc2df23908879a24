import asyncio
import random
import time

from cursory import generate_trajectory
from selenium_driverless import webdriver  # type: ignore[import-untyped]
from selenium_driverless.utils.utils import read  # type: ignore[import-untyped]


async def main() -> None:
    """Run a sample test using Selenium."""
    async with webdriver.Chrome() as driver:
        await driver.get("https://google.com/blank.html", wait_load=True)
        # Inject mouse tracking script
        await driver.execute_script(script=await read("/files/js/show_mousemove.js", sel_root=True))

        end_point = (0, 0)
        for _ in range(10):
            # Generate a random trajectory
            start_point = end_point
            end_point = (random.randint(0, 1080), random.randint(0, 720))
            trajectory_points, timings = generate_trajectory(start_point, end_point, frequency=100)
            # Print Frequency
            duration = timings[-1] - timings[0]
            if duration > 0:
                avg_freq = 1000 / duration * len(timings)
                print(f"Average Frequency: {avg_freq:.2f} Hz")

            # Simulate drawing the trajectory on the page
            pointer = driver.current_pointer
            previous_timing = 0
            for point, timing in zip(trajectory_points, timings, strict=True):
                # Move mouse to the point and measure delay
                start = time.time()
                await pointer.base.move_to(x=point[0], y=point[1])
                delay_time = (time.time() - start) * 1000

                # Wait based on the gap to the previous point, never on absolute time
                pause_time = timing - previous_timing
                previous_timing = timing
                if pause_time <= 0:
                    # No positive waiting time for this point; skip waiting.
                    continue
                if delay_time > pause_time:
                    print(f"[WARNING]: Delay ({delay_time:.2f} ms) is greater than timing pause ({pause_time} ms)")
                await asyncio.sleep(max((pause_time - delay_time) / 1000, 0))

            # Click at the end point
            await pointer.base.click(x=end_point[0], y=end_point[1])
            await asyncio.sleep(1)


asyncio.run(main())
