import datetime
import os
import sys
import time
import warnings
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

import cv2
import numpy as np
import pyautogui
from PIL import Image

from rpapy.core.utils.messages import confirm_yes_no_cancel

with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from pywinauto import Desktop

from rpapy.core.image_mapper import map_images
from rpapy.core.locator_outcome import LocatorOutcome, is_locator_outcome
from rpapy.core.snipps.loads import create_python_default_dirs

from .config import Config
from .snipps import update_image
from .snipps.snippingtools import (ImageNotDisappearError, ImageNotFoundError,
                                   close_window_with_title)

MAINTENANCE_MODE = False
if Config.CHECK_MODE:
    MAINTENANCE_MODE = confirm_yes_no_cancel(title='RPAPY',text='Ativar o modo de manutenção?')

if MAINTENANCE_MODE is None:
    pyautogui.hotkey('ctrl', 'win', 'x')
    pyautogui.sleep(2)
    exit()

if MAINTENANCE_MODE is False:
    pyautogui.FAILSAFE = False

if Config.FAILSAFE_OFF:
    pyautogui.FAILSAFE = False

images_dir_path =  Config.IMAGES_DIR_PATH
error_images_dir_path = Config.ERROR_IMAGES_DIR_PATH


def locate_on_screen(image, *args, **kwargs):
    try:
        img = Image.open(image)
        return pyautogui.locateOnScreen(img, *args, **kwargs)
    except ValueError:
        pass


def get_absolute_path_by_image_name(image_name: str) -> Optional[str]:
    """Return the template path mapped to *image_name*, or None if unknown."""
    try:
        return map_images().get(image_name, {}).get('image')
    except Exception:
        return None


class LocatorImage():
    """Locates template images on the screen.

    Failed lookups return a :class:`rpapy.core.locator_outcome.LocatorOutcome`
    instead of raising, so callers can inspect what happened (kind, image name,
    waited time and template path).  A successful lookup returns an
    ``(x, y)`` coordinate tuple as before.

    Dependencies can be injected (keyword arguments on ``__init__`` or public
    attributes) to exercise the logic with fake screenshots and image maps in
    tests:

    * ``screenshots`` -- image map, same shape as :func:`map_images`.
    * ``locate_func`` -- ``(image=..., region=..., [confidence=...]) -> coordinate``.
    * ``screenshot_func`` -- ``() -> PIL.Image`` used for error screenshots.
    * ``map_images_func`` -- ``() -> image map`` used after re-captures.
    * ``update_image_func`` -- ``(image_path) -> bool|None`` re-capture hook.
    * ``time_func`` / ``sleep_func`` -- clock and sleep used for timing.
    * ``center_func`` -- converts a locate result into an ``(x, y)`` center.
    * ``error_save_func`` -- saves the error screenshot, returns its path.
    * ``alert_func`` -- confirmation dialog used in maintenance mode.
    * ``show_func`` -- opens the template image in maintenance mode.
    * ``move_mouse`` -- set to False to skip moving the real cursor.
    """

    def __init__(self,
                 screenshots: Optional[Dict[str, Dict[str, Tuple[int]]]] = None,
                 *,
                 locate_func: Optional[Callable] = None,
                 screenshot_func: Optional[Callable] = None,
                 map_images_func: Optional[Callable] = None,
                 update_image_func: Optional[Callable] = None,
                 time_func: Optional[Callable[[], float]] = None,
                 sleep_func: Optional[Callable[[float], None]] = None,
                 center_func: Optional[Callable] = None,
                 error_save_func: Optional[Callable] = None,
                 alert_func: Optional[Callable] = None,
                 show_func: Optional[Callable] = None,
                 maintenance_mode: Optional[bool] = None,
                 move_mouse: bool = True):
        # Kept as None when not injected so module-level patches (e.g. in
        # tests) are picked up at call time instead of being frozen here.
        self.map_images_func = map_images_func
        if screenshots is None:
            self._screenshots: Dict[str, Dict[str, Tuple[int]]] = self._map_images()
        else:
            self._screenshots = screenshots

        self.locate_func = locate_func
        self.screenshot_func = screenshot_func
        self.update_image_func = update_image_func
        self.time_func = time_func
        self.sleep_func = sleep_func
        self.center_func = center_func
        self.error_save_func = error_save_func
        self.alert_func = alert_func
        self.show_func = show_func
        self.move_mouse = move_mouse

        self.wait_before = 0.0
        self.debug = False
        self._interval = 0.2
        self._max_wait = Config.MAX_WAIT_MAINTENANCE if MAINTENANCE_MODE else 30
        self._maintenance_mode = MAINTENANCE_MODE if maintenance_mode is None else maintenance_mode

    def modo_manutencao(self, option: bool):
        if isinstance(option, bool):
            if option:
                self._maintenance_mode = option
                self._max_wait = 10
            else:
                self._maintenance_mode = option
                self._max_wait = 30

    @property
    def max_wait(self):
        return self._max_wait

    @max_wait.setter
    def max_wait(self, _max_wait):
        max_wait = float(_max_wait)
        if max_wait >= 0:
            self._max_wait = max_wait
        else:
            self._max_wait = 30

    @property
    def interval(self):
        return self._interval

    @interval.setter
    def interval(self, interval):
        if interval >= 0:
            self._interval = interval

    @property
    def screenshots(self):
        return self._screenshots

    @screenshots.setter
    def screenshots(self, value):
        self._screenshots = value

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _now(self) -> float:
        time_func = self.time_func or time.time
        return time_func()

    def _sleep(self, seconds: float) -> None:
        sleep_func = self.sleep_func or time.sleep
        sleep_func(seconds)

    def _map_images(self):
        map_images_func = self.map_images_func or map_images
        return map_images_func()

    def _locate_on_screen(self, image_region: Dict[str, object],
                          confidence: Optional[float]):
        locate_func = self.locate_func or locate_on_screen
        if confidence is None:
            return locate_func(**image_region)
        return locate_func(**image_region, confidence=confidence)

    def _screenshot(self):
        screenshot_func = self.screenshot_func or pyautogui.screenshot
        return screenshot_func()

    def _center_of(self, coordinate):
        center_func = self.center_func or pyautogui.center
        return center_func(coordinate)

    def _update_image(self, image_path: str):
        update_image_func = self.update_image_func or update_image
        return update_image_func(image_path)

    def _save_error_screenshot(self, image_name: str, screenshot) -> Optional[str]:
        error_save_func = self.error_save_func or salvar_img_error
        return error_save_func(image_name, screenshot)

    def _alert(self, title: str, text: str):
        alert_func = self.alert_func or pyautogui.alert
        return alert_func(title=title, text=text)

    def _show_image(self, image_path: str) -> None:
        show_func = self.show_func
        try:
            if show_func is not None:
                show_func(image_path)
            else:
                Image.open(image_path).show()
        except Exception:
            # A viewer that cannot open must not break the lookup.
            pass

    def _get_image_region(self, image_name: str) -> Dict[str, object]:
        data = self._screenshots[image_name]
        return {'image': data['image'], 'region': data['region']}

    def _get_image_path(self, image_name: str) -> Optional[str]:
        try:
            return self._screenshots[image_name]['image']
        except (KeyError, TypeError):
            return None

    def _center(self, coordinate) -> Tuple[int, int]:
        """Return the center of a locate result as an ``(x, y)`` tuple."""
        try:
            center = self._center_of(coordinate)
        except Exception:
            center = coordinate
        x, y = center[0], center[-1]
        return int(x), int(y)

    def _remap(self) -> None:
        self._screenshots = self._map_images()

    def _outcome(self, kind: str, image_name: str, image_path: Optional[str],
                 elapsed: float, max_wait: float, wait_vanish: bool,
                 *, error_image_path: Optional[str] = None,
                 total_elapsed: Optional[float] = None) -> LocatorOutcome:
        return LocatorOutcome(
            kind=kind,
            image_name=image_name,
            image_path=image_path,
            elapsed_seconds=elapsed,
            max_wait=max_wait,
            wait_vanish=wait_vanish,
            maintenance_mode=self._maintenance_mode,
            error_image_path=error_image_path,
            total_elapsed_seconds=elapsed if total_elapsed is None else total_elapsed,
        )

    def _resolve_interval(self, interval: Optional[float]) -> float:
        if interval is not None and interval > 0:
            return interval
        return self._interval

    def _resolve_max_wait(self, max_wait: Optional[float], interval_local: float) -> float:
        # A positive max_wait always wins.  The old code silently ignored a
        # max_wait smaller than the polling interval, which made short waits
        # unexpectedly fall back to the default (30s / maintenance).
        if max_wait is not None:
            max_wait = float(max_wait)
            if max_wait > 0:
                return max_wait
        return self._max_wait

    def _poll_sleep(self, elapsed: float, interval_local: float,
                    max_wait_local: float) -> None:
        """Sleep between polls without overshooting the deadline."""
        remaining = max_wait_local - elapsed
        if remaining <= 0:
            return
        self._sleep(min(interval_local, remaining))

    def _save_error_image(self, image_name: str) -> Optional[str]:
        """Save a diagnostic screenshot and return its path (or None)."""
        try:
            screenshot = self._screenshot()
            return self._save_error_screenshot(image_name, screenshot)
        except Exception:
            return None

    def _has_image(self, image_name: str) -> bool:
        try:
            return isinstance(self._screenshots, dict) and image_name in self._screenshots
        except Exception:
            return False

    def _ensure_default_template(self, image_name: str) -> bool:
        """Create a placeholder template so the name exists in the image map.

        Returns True when *image_name* is mapped after the refresh.
        """
        default_image = os.path.join(
            os.path.abspath(os.path.dirname(__file__)),
            'utils/imagens/IMAGEM_NAO_ENCONTRADA.png'
        )
        try:
            im = Image.open(default_image)
            im.save(f'{images_dir_path}/{image_name}-(300, 50, 700, 200).png', 'PNG')
            self._sleep(1)
            self._remap()
        except Exception:
            return self._has_image(image_name)
        return self._has_image(image_name)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def __call__(self,
                 image_name: str,
                 *,
                 max_wait: float=None,
                 confidence: float=None,
                 wait_vanish=False,
                 interval: float=None,
                 deslocar_x:int=0,
                 deslocar_y:int=0,
                 ignore_error: bool=False,
                 raise_on_error: bool=False):

        """Searches the image on the screen until the maximum time defined by parameter.

        Arguments:
            image_name {str} -- name of the image to be retrieved from the dictionary

        Keyword Arguments:
            max_wait {float} -- maximum waiting time to find the image (default: {None})
            interval {float} -- stop interval for image search (default: {None})
            deslocar_x {int} -- shifts the center of the image location on the x-axis (default: {0})
            deslocar_y {int} -- shifts the center of the image location on the y-axis (default: {0})
            wait_vanish {bool} -- waits for the image to disappear instead of appear (default: {False})
            ignore_error {bool} -- kept for backwards compatibility; failures are
                always reported through a LocatorOutcome, never raised by this
                method (default: {False})
            raise_on_error {bool} -- opt-in legacy behaviour: raise
                ImageNotFoundError / ImageNotDisappearError instead of returning
                the outcome (default: {False})

        Returns:
            Tuple[int] -- on success, the ``(x, y)`` center of the found image
                plus the displacements.  For ``wait_vanish=True`` the success
                value is ``None`` (the image disappeared), as before.
            LocatorOutcome -- on failure, describing whether the image was not
                found in time, did not disappear in time, or the maintenance
                re-capture was not completed.  It carries ``image_name``,
                ``image_path`` and ``elapsed_seconds``.
        """
        # If True performs a stop at each execution. Used to reduce the speed of the bot.
        if self.debug:
            input(f'Localizar "{image_name}" - Pressione enter para continuar...')

        interval_local = self._resolve_interval(interval)
        max_wait_local = self._resolve_max_wait(max_wait, interval_local)

        # Pauses before trying to find the image location.
        self._sleep(self.wait_before)

        # Moves the mouse to a resting point on the screen so as not to interfere with the image search.
        if self.move_mouse:
            pyautogui.moveTo(700,5,0)

        # Register the start of the lookup so the total time (including any
        # maintenance re-captures) can be reported.
        call_start = self._now()

        if not wait_vanish:
            result = self._wait_appear(
                image_name, confidence, interval_local, max_wait_local,
                deslocar_x, deslocar_y, ignore_error, call_start
            )
        else:
            result = self._wait_vanish(
                image_name, confidence, interval_local, max_wait_local,
                ignore_error, call_start
            )

        if is_locator_outcome(result):
            # Record the wall-clock total once, without extra clock reads in
            # the hot loop.
            result.total_elapsed_seconds = self._now() - call_start
            if raise_on_error:
                self._raise_for_outcome(result)
        return result

    @staticmethod
    def _raise_for_outcome(outcome: LocatorOutcome) -> None:
        message = str(outcome)
        if outcome.kind == LocatorOutcome.WAIT_VANISH_TIMEOUT:
            error = ImageNotDisappearError(message)
        else:
            error = ImageNotFoundError(message)
        error.outcome = outcome
        raise error

    def _wait_appear(self, image_name: str, confidence: Optional[float],
                     interval_local: float, max_wait_local: float,
                     deslocar_x: int, deslocar_y: int, ignore_error: bool,
                     call_start: float):
        """Wait until the image appears; return the coordinate or an outcome."""

        image_path = self._get_image_path(image_name)

        # Timer for the current waiting attempt. It is reset every time the
        # template is re-captured in maintenance mode, so time spent before the
        # re-capture never counts towards the new attempt.
        attempt_start = call_start

        while True:
            try:
                image_region = self._get_image_region(image_name)
            except (KeyError, TypeError):
                # Create a file with image_name in the images directory if it does not exist
                if not self._ensure_default_template(image_name):
                    # The image still cannot be mapped: report instead of looping forever.
                    return self._outcome(
                        LocatorOutcome.TIMEOUT_NOT_FOUND,
                        image_name, image_path,
                        self._now() - attempt_start, max_wait_local,
                        wait_vanish=False,
                    )
                image_path = self._get_image_path(image_name) or image_path
                # The placeholder creation may take a while; only count the
                # time spent waiting for the image itself.
                attempt_start = self._now()
                continue

            image_path = image_region.get('image') or image_path

            try:
                # Executes the else block if the image is found on the map and continues to exit the loop.
                coordinate = self._locate_on_screen(image_region, confidence)
            except Exception:
                coordinate = None

            if coordinate is not None:
                # Retrieves the x,y coordinate of the center of the found image.
                center_x, center_y = self._center(coordinate)

                # Retrieves the anchor coordinates to apply the displacement
                try:
                    anchor_coord = self._screenshots[image_name].get('anchor_coord')
                except (KeyError, TypeError):
                    anchor_coord = None
                if anchor_coord is not None and deslocar_x + deslocar_y == 0:
                    deslocar_x, deslocar_y = anchor_coord

                # Returns the result adding the displacements, if passed by parameter.
                return center_x + int(deslocar_x), center_y + int(deslocar_y)

            elapsed = self._now() - attempt_start

            # Enter block if loop execution time exceeds max_wait.
            if elapsed >= max_wait_local:
                if self._maintenance_mode:
                    atualizou = self._update_template(image_name, image_path)
                    if atualizou:
                        # Re-map the images and restart the timer: the time
                        # spent before the re-capture belongs to the old wait.
                        self._remap()
                        image_path = self._get_image_path(image_name) or image_path
                        attempt_start = self._now()
                        elapsed = 0.0
                    else:
                        # The user cancelled/did not finish the re-capture:
                        # report it right away instead of looping forever.
                        return self._outcome(
                            LocatorOutcome.MAINTENANCE_ABORTED,
                            image_name, image_path, elapsed, max_wait_local,
                            wait_vanish=False,
                        )
                else:
                    error_image_path = None
                    if not ignore_error:
                        # Take a screenshot of the screen at the time the error occurred.
                        error_image_path = self._save_error_image(image_name)
                    return self._outcome(
                        LocatorOutcome.TIMEOUT_NOT_FOUND,
                        image_name, image_path, elapsed, max_wait_local,
                        wait_vanish=False,
                        error_image_path=error_image_path,
                    )

            # Take a timeout between loops trying to find the image.
            self._poll_sleep(elapsed, interval_local, max_wait_local)

    def _wait_vanish(self, image_name: str, confidence: Optional[float],
                     interval_local: float, max_wait_local: float,
                     ignore_error: bool, call_start: float):
        """Wait until the image disappears; return None or an outcome."""

        image_path = self._get_image_path(image_name)
        attempt_start = call_start

        while True:
            try:
                image_region = self._get_image_region(image_name)
            except (KeyError, TypeError):
                # If maintenance mode equals True, creates a provisional image
                # and updates the newly created image on the map.
                if self._maintenance_mode:
                    if not self._ensure_default_template(image_name):
                        return self._outcome(
                            LocatorOutcome.MAINTENANCE_ABORTED,
                            image_name, image_path, self._now() - attempt_start,
                            max_wait_local, wait_vanish=True,
                        )
                    image_path = self._get_image_path(image_name) or image_path
                    atualizou = self._update_template(image_name, image_path)
                    if not atualizou:
                        return self._outcome(
                            LocatorOutcome.MAINTENANCE_ABORTED,
                            image_name, image_path, self._now() - attempt_start,
                            max_wait_local, wait_vanish=True,
                        )
                    self._remap()
                    image_path = self._get_image_path(image_name) or image_path
                    attempt_start = self._now()
                    continue
                return self._outcome(
                    LocatorOutcome.TIMEOUT_NOT_FOUND,
                    image_name, image_path, self._now() - attempt_start,
                    max_wait_local, wait_vanish=True,
                    error_image_path=None if ignore_error else self._save_error_image(image_name),
                )

            image_path = image_region.get('image') or image_path

            try:
                coordinate = self._locate_on_screen(image_region, confidence)
            except Exception:
                coordinate = None

            # The image is gone.
            if coordinate is None:
                return None

            elapsed = self._now() - attempt_start

            if elapsed >= max_wait_local:
                if self._maintenance_mode:
                    # Open the image that did not disappear within the maximum waiting time.
                    self._show_image(image_region['image'])
                    answered = self._alert(
                        title='RPAPY',
                        text='A imagem {} não desapareceu em {:.0f} minutos! '
                             'Feche-a para continuar a manutenção.'.format(
                                 image_region['image'], elapsed / 60)
                    )
                    # Closes image viewer window after switching confirmation.
                    try:
                        close_image_viewer('fotos')
                    except Exception:
                        pass
                    if answered is None:
                        # The user dismissed the alert: report it instead of
                        # waiting forever.
                        return self._outcome(
                            LocatorOutcome.MAINTENANCE_ABORTED,
                            image_name, image_path, elapsed, max_wait_local,
                            wait_vanish=True,
                        )
                    # The user acknowledged: restart the timer for the new wait.
                    attempt_start = self._now()
                    elapsed = 0.0
                else:
                    return self._outcome(
                        LocatorOutcome.WAIT_VANISH_TIMEOUT,
                        image_name, image_path, elapsed, max_wait_local,
                        wait_vanish=True,
                    )

            # Performs a pause between loops trying to find the image
            self._poll_sleep(elapsed, interval_local, max_wait_local)

    def _update_template(self, image_name: str, image_path: Optional[str]):
        """Ask the user to re-capture the template.

        Returns True when a new template was saved, False/None when the user
        cancelled or did not finish (the old loop used to keep retrying
        forever in that case).
        """
        path = image_path or self._get_image_path(image_name)
        if not path:
            return False
        try:
            return self._update_image(path)
        except (ImageNotFoundError, ImageNotDisappearError):
            # Backwards compatibility with maintenance flows that signal a
            # cancelled re-capture by raising.
            return False
        except Exception as error:
            # A failing capture dialog must not turn into an endless wait.
            try:
                print(f'[RPAPY][MAINTENANCE] re-capture failed: {error!r}',
                      file=sys.stderr)
            except Exception:
                pass
            return False


def close_image_viewer(title: str):
    """Closes the Windows Image Viewer window

    Arguments:
        title {str} -- Name or part of the name of the title of the image viewer window.
    """
    image_viewer = pyautogui.getWindowsWithTitle(title)
    for v in image_viewer:
        v.close()


def salvar_img_error(image_name:str, im:Image) -> Optional[str]:
    """Saves a screenshot of the moment the error occurred with information
    date and time and name of the image where the error was thrown.

    Arguments:
        image_name {str} -- name of the image that was not found by the search method.
        im {Image} -- image that was captured at the time of the error.

    Returns:
        str -- absolute path of the saved error screenshot, or None if it
            could not be saved.
    """
    try:
        create_python_default_dirs()
        nome_arquivo = datetime.datetime.now().strftime('%Y.%m.%d-%Hh%Mm%Ss')+'_'+image_name
        file_path = Path(error_images_dir_path) / f'{nome_arquivo}.png'
        im.save(file_path.as_posix(), 'PNG')
        return file_path.as_posix()
    except Exception:
        return None


def max_wait_attr(desktop: Desktop, attr_name: str, max_wait: float=5.0) -> AttributeError:
    start_time = time.time()
    while True:
        if hasattr(desktop, attr_name):
            break
        elif time.time() - start_time > max_wait:
            raise AttributeError(f'O atributo "{attr_name}" não foi encontrado!')
        time.sleep(.1)


def image_optmization(im: Image) -> Image:
    """Fonte: https://blog.codeexpertslearning.com.br/lendo-imagens-uma-abordagem-à-ocr-com-google-tesseract-e-python-ee8e8009f2ab
    Pesquisado em: 02/01/2020
    """
    from PIL import Image

    # Typing the reading for RGB order channels.
    imagem = im.convert('RGB')

    # Converting to an editable array of numpy[x, y, CANALS]
    npimagem = np.asarray(imagem).astype(np.uint8)

    # Noise reduction before binarization.
    npimagem[:, :, 0] = 0       # canal R (RED).
    npimagem[:, :, 2] = 0       # canal B (BLUE).

    # Grayscale Attribution
    im = cv2.cvtColor(npimagem, cv2.COLOR_RGB2GRAY)

    # Application of binary truncation for intensity
    # Color intensity pixels below 127 will be converted to 0 (BLACK).
    # Color intensity pixels above 127 will be converted to 255 (WHITE).
    # The THRESH_OTSU attribute increases intelligent level analysis truncation.
    ret, thresh = cv2.threshold(im, 127, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)

    # Reconverting the threshold return into an object of type PIL.Image
    binimagem = Image.fromarray(thresh)

    # Call to tesseract OCR through its wrapper
    # phrase = ocr.image_to_string(binimagem, lang='por')

    # printout of result
    # print(phrase)

    return binimagem

if '__main__' == __name__:
    map_images()
