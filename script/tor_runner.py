'''
@TODO
Converting the result of parsing to pyAutoGUI method.
'''
from api.api import AutoGuiWrapper
from detect.img_detector import ImgDetector
from script.tor_parser import parse_result, Function


def param_to_str(param):
    return str(param)


def param_to_float(param):
    return float(param)


class TargetNotFoundError(Exception):
    '''Raised when a Click target image cannot be located on the screen.'''

    def __init__(self, line_no, command_text):
        self.line_no = line_no
        self.command_text = command_text
        super().__init__(
            "Target not found at line %d: %s" % (line_no, command_text))


class RunResult:
    '''Outcome of executing a script, returned to the caller.'''

    def __init__(self, success, line_no=None, command_text=None, reason=None):
        self.success = success
        self.line_no = line_no
        self.command_text = command_text
        self.reason = reason

    def __repr__(self):
        if self.success:
            return "RunResult(success=True)"
        return ("RunResult(success=False, line_no=%r, command_text=%r, "
                "reason=%r)" % (self.line_no, self.command_text, self.reason))


class RunScript:

    def __init__(self, workspace_path, auto=None, detector=None):
        self.auto = auto if auto is not None else AutoGuiWrapper()
        self.workspace_path = workspace_path
        self.detector = detector if detector is not None \
            else ImgDetector(workspace_path)

    def traverse(self, f, line_no=None, line_text=None):
        if not f:
            return
        func_or_comment = f[0]
        if isinstance(func_or_comment, Function):
            command = f[0].command
            if hasattr(func_or_comment, 'param'):
                param = f[0].param.name

                if str(command).lower() == 'click':
                    filename = param
                    file_extension = '.png'

                    target_img_path = self.workspace_path + '/' + filename + file_extension
                    coord = self.detector.img_coord_detector_by_target(target_img_path)

                    if coord is None:
                        # Target not found: skip the click and abort the
                        # script instead of clicking at a wrong position.
                        raise TargetNotFoundError(line_no, line_text)

                    self.auto.click(coord)
                elif str(command).lower() == 'wait':
                    self.auto.wait(param_to_float(param))
                elif str(command).lower() == 'type':
                    self.auto.type(param_to_str(param))
                elif str(command).lower() == 'press':
                    self.auto.press(param_to_str(param))

                print(command, param)

    def exec_script(self, file_path):
        '''
        Execute every line of the script.

        Returns a RunResult: success=True when all commands ran, or
        success=False with the line number / command text when the script
        was aborted because a Click target was not found.
        '''
        # Read the data
        with open(file_path, 'r') as source_file:
            data = source_file.read()

        # Attempt to parse it, or throw errors
        for line_no, line in enumerate(data.splitlines(), start=1):
            f = parse_result(line)
            try:
                self.traverse(f, line_no=line_no, line_text=line.strip())
            except TargetNotFoundError as e:
                print("Script aborted: " + str(e))
                return RunResult(False, line_no=e.line_no,
                                 command_text=e.command_text,
                                 reason=str(e))

        return RunResult(True)


def run(workspace_path):
    file_path = workspace_path + '/' + 'script.tor'
    run = RunScript(workspace_path)
    return run.exec_script(file_path)
