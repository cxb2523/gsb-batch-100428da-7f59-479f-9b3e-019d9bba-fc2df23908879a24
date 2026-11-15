![](https://i.imgur.com/ClOul8Y.png)

# RPAPY - Open Source Tool for Robotic Process Automatition
[Medium](https://medium.com/@codigo100cera) | [Linkedin](https://www.linkedin.com/in/mcsilva-csc/) | [Youtube](https://www.youtube.com/channel/UCbrw7-reRWpQD1JBubCl4QQ?view_as=subscriber) | [Telegram](https://t.me/joinchat/C_ECNVWoSae_ebzcjfM33w)

__RPAPY__ is a open source easy tool for automating boring stuffs on any screen with robotframework, pyautogui, pywinauto and others.

_"Creating __automations__ for __GUI__ has never been more __fun__ than now."_


![RPAPY Example](https://i.imgur.com/9TaGCby.gif)

## Get started

### Windows

The easiest way to install RPAPY is by using __pip install__

- Download and install [Python >=3.10, <3.13 ](https://www.python.org)

- Install the latest version RPAPY on your machine:
```
pip install rpapy
```

However, it is advisable to use a virtual environment for an isolated installation of all requirements, this avoids conflicts with the python interpreter installed on the host machine:
```
pip install virtualenv
mkdir <your-project-name>
cd <your-project-name>
virtualenv create rpapy-venv
activate rpapy-venv
pip install rpapy
```

The construction of the project structure is accomplished by executing the following command in the terminal:
```
rpapy
```

The hotkeys menu to be used will be displayed after executing the above command.

```
***TURN ON AGENTPY***
        ****************HOTKEYS MENU****************
        <CTRL>+<ALT>+P: Capture image or ocr
        <CTRL>+<ALT>+R: Change file name
        <CTRL>+<ALT>+B: Switch between backends
        <CTRL>+I:       Inspect UI element
        <CTRL>+<CMD>+C: View current configuration
        <CTRL>+<CMD>+E: Load sample implementation
        <CTRL>+<CMD>+X: Turn off agent
        ********************************************
```

To run the sample implementation, perform the following steps:

- Configure the .env file in the root directory of the project.

```
###VARIAVEIS DE AMBIENTE PYTHON-DOTENV

#RESOURCES_DIR_NAME=resources
#RESOURCES_KEYWORDS_FILE_NAME=keywords.robot
#IMAGES_DIR_NAME=images
#IMAGES_ERROR_DIR_NAME=images_error
#TASKS_DIR_NAME=tasks
#MAX_WAIT_MANUTENCAO=5
#VERIFICAR_MODO=True
#FAILSAFE_OFF=True
#ARQUIVO_TEMPORARIO_ATIVO=False
#NOME_ARQUIVO_TEMPORARIO=temp.txt
```

- Run the following command on the terminal:

```
robot -d log tasks
```

- In the dialog box, which will be displayed, choose the option YES and make changes to the images as required by RPAPY.

- After the update cycle of the UI images in which the actions will be performed by the robot, interrupt the execution.

- Repet the command on the terminal:

```
robot -d log tasks
```

- select the option no in the dialog box to choose the maintenance mode off.

## Locating images on screen

`LocatorImage.__call__` returns the ``(x, y)`` coordinate of the element on
success. When the element cannot be located it returns a
`rpapy.core.locator_outcome.LocatorOutcome` instead of raising, so the caller
can inspect what happened:

| `outcome.kind` | Meaning |
| --- | --- |
| `timeout_not_found` | The image did not appear before `max_wait` expired. |
| `wait_vanish_timeout` | `wait_vanish=True` and the image was still on screen when `max_wait` expired. |
| `maintenance_aborted` | Maintenance mode asked for a new capture, but the user cancelled/did not finish. |

Every outcome carries `image_name`, `image_path` (the template that was used),
`elapsed_seconds` (the time actually spent in the current wait — the timer is
reset after a maintenance re-capture) and, for a plain timeout, the path of the
diagnostic screenshot in `error_image_path`.

```python
from rpapy.core.locator import LocatorImage
from rpapy.core.locator_outcome import is_locator_outcome

locator = LocatorImage()
result = locator('btn_salvar', max_wait=10)
if is_locator_outcome(result):
    print(result.kind, result.image_name, result.elapsed_seconds, result.image_path)
else:
    pyautogui.click(*result)
```

- `ignore_error=True` never raises; a failed lookup is returned as an outcome.
- `raise_on_error=True` restores the old behaviour (`ImageNotFoundError` /
  `ImageNotDisappearError`) for callers that prefer exceptions.

`get_element_vision` (and the `*_vision` helpers built on it) detect an
outcome and stop before clicking/typing with it: the failure is printed to
stderr and raised unless `ignore_error=True`, in which case the outcome is
returned to the caller.

## Credits

Under the hood, RPAPY is built on some of the greatest open source libraries. Within RPAPY, the following libraries are currently included:

- [Keyring](https://pypi.org/project/keyring/)
- [Mouse](https://github.com/boppreh/mouse)
- [Numpy](https://pypi.org/project/numpy/)
- [OpencvPython](https://pypi.org/project/opencv-python/)
- [Pillow](https://pillow.readthedocs.io/en/stable/)
- [PyAutoGUI](https://github.com/asweigart/pyautogui)
- [Pynput](https://pypi.org/project/pynput/)
- [Pyperclip](https://pypi.org/project/pyperclip/)
- [PySide6](https://pypi.org/project/PySide2/)
- [PySimpleGUI](https://pypi.org/project/PySimpleGUI/)
- [Pytesseract](https://pypi.org/project/pytesseract/)
- [PythonDotenv](https://pypi.org/project/python-dotenv/)
- [Pwinauto](https://pypi.org/project/pywinauto/)
- [RobotFramework](https://pypi.org/project/robotframework/)
