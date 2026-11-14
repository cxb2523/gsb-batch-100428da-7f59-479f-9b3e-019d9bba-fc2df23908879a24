# Importing necessary modules and functions
import sys

from crawler.crawler import CrawlEngine
from crawler.libs.internals.custom_logger import ERR, log_says
from crawler.libs.internals.debug import print_stack_trace
from crawler.libs.internals.flags import VERBOSE, DEBUG
from crawler.libs.internals.timer import XTimer
from src.crawler.libs.internals.crawler_exceptions import CrawlerException


# Function to start the crawling process
def run():
    """
    This function initializes the CrawlEngine and starts the crawling process.
    """
    ce = CrawlEngine()
    ce.start()


# Main execution of the code
if __name__ == "__main__":
    with XTimer("MyTimer"):  # Timer to measure the execution time
        try:
            run()  # Start the crawling process
        except (SystemExit, KeyboardInterrupt):
            # Exit the program gracefully if interrupted
            sys.exit(0)
        except CrawlerException as err:
            # Handle CrawlerException and log the error message if VERBOSE level is 3 or more
            if VERBOSE >= 3:
                log_says(message=err.message, log_type=ERR, agent="main.py")
            sys.exit(1)
        except:
            # If any other exception occurs, print the stack trace if DEBUG is True
            if DEBUG:
                print_stack_trace()
            sys.exit(2)
        else:
            pass
        finally:
            # Ensure the program exits even if an unhandled exception occurs
            sys.exit(0)
