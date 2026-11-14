import sys
import traceback
from io import StringIO

import termcolor as tc


def print_stack_trace():
    """
    Prints the stack trace of the programme when an error occurred
    :return: None
    """

    try:
        ct = StringIO()
        ct.seek(0)
        traceback.print_exc(file=ct)
    except:
        traceback.print_exc(file=sys.stderr)
    else:
        tc.cprint("\nERROR:", "red")
        tc.cprint(str(ct.getvalue()), "yellow")
