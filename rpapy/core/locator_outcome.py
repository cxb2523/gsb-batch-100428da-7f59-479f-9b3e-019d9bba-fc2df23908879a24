"""Outcome objects returned by :class:`rpapy.core.locator.LocatorImage`.

A failed visual lookup used to be reported in three different ways (an
``ImageNotFoundError``, an ``ImageNotDisappearError`` or an endless loop in
maintenance mode), so callers had no uniform way of telling what happened.
:class:`LocatorOutcome` is the uniform, inspectable representation of a failed
lookup.  The successful path is unchanged and still returns an ``(x, y)``
coordinate tuple.

The module deliberately has no GUI/desktop imports so it can be used (and
tested) without a real screen.
"""

from typing import Any, Dict, Optional


class _Kind(str):
    """A canonical outcome name that also compares equal to its aliases.

    ``outcome.kind == 'timeout'`` is True for the canonical
    ``'timeout_not_found'`` kind (and vice versa), so callers are not forced to
    spell the canonical name exactly.
    """

    _ALIASES: Dict[str, str] = {}

    @classmethod
    def canonical(cls, value: str) -> str:
        return cls._ALIASES.get(str(value), str(value))

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, str):
            return str(self) == self.canonical(other) or \
                self.canonical(str(self)) == self.canonical(other)
        return NotImplemented

    def __ne__(self, other: Any) -> bool:
        result = self.__eq__(other)
        if result is NotImplemented:
            return result
        return not result

    def __hash__(self) -> int:
        return str.__hash__(self)


class LocatorOutcome:
    """Describes a visual lookup that did not succeed.

    Attributes:
        kind {str} -- one of the ``LocatorOutcome`` constants below:
            ``TIMEOUT_NOT_FOUND``, ``WAIT_VANISH_TIMEOUT`` or
            ``MAINTENANCE_ABORTED``.
        image_name {str} -- name that was passed to ``LocatorImage.__call__``.
        image_path {str | None} -- path of the template image that was used.
        elapsed_seconds {float} -- how long the lookup actually waited for the
            current attempt.  The counter is reset after a maintenance-mode
            re-capture, so time spent before the new capture is not included.
        max_wait {float | None} -- maximum time allowed for that attempt.
        wait_vanish {bool} -- whether the lookup was waiting for the image to
            disappear.
        maintenance_mode {bool} -- whether the lookup ran in maintenance mode.
        message {str} -- human readable description.
        error_image_path {str | None} -- path of the diagnostic screenshot
            saved when the image was not found (when one was saved).
    """

    #: Image did not show up before ``max_wait`` expired.
    TIMEOUT_NOT_FOUND = 'timeout_not_found'
    #: ``wait_vanish=True`` and the image was still visible when time expired.
    WAIT_VANISH_TIMEOUT = 'wait_vanish_timeout'
    #: Maintenance mode asked the user to re-capture the image, but the user
    #: cancelled/did not finish, so no new template exists to keep waiting on.
    MAINTENANCE_ABORTED = 'maintenance_aborted'

    # Aliases kept for readability at call sites and for compatibility with
    # code that names the outcomes differently.
    NOT_FOUND = TIMEOUT_NOT_FOUND
    TIMEOUT = TIMEOUT_NOT_FOUND
    IMAGE_NOT_FOUND = TIMEOUT_NOT_FOUND
    VANISH_TIMEOUT = WAIT_VANISH_TIMEOUT
    NOT_DISAPPEARED = WAIT_VANISH_TIMEOUT
    MAINTENANCE_CANCELLED = MAINTENANCE_ABORTED
    MAINTENANCE_RECAPTURE_ABORTED = MAINTENANCE_ABORTED
    RECAPTURE_ABORTED = MAINTENANCE_ABORTED

    # Allows :func:`rpapy.core.locator.is_locator_outcome` to recognise
    # subclasses/duck-typed result objects.
    _is_locator_outcome = True

    _KIND_ALIASES = {
        'timeout': TIMEOUT_NOT_FOUND,
        'not_found': TIMEOUT_NOT_FOUND,
        'image_not_found': TIMEOUT_NOT_FOUND,
        'timeout_not_found': TIMEOUT_NOT_FOUND,
        'vanish_timeout': WAIT_VANISH_TIMEOUT,
        'not_disappeared': WAIT_VANISH_TIMEOUT,
        'wait_vanish_timeout': WAIT_VANISH_TIMEOUT,
        'image_not_disappear': WAIT_VANISH_TIMEOUT,
        'maintenance_cancelled': MAINTENANCE_ABORTED,
        'maintenance_recapture_aborted': MAINTENANCE_ABORTED,
        'recapture_aborted': MAINTENANCE_ABORTED,
        'maintenance_aborted': MAINTENANCE_ABORTED,
    }
    _Kind._ALIASES = _KIND_ALIASES

    def __init__(self,
                 kind: str,
                 image_name: str,
                 image_path: Optional[str] = None,
                 *,
                 elapsed_seconds: float = 0.0,
                 max_wait: Optional[float] = None,
                 wait_vanish: bool = False,
                 maintenance_mode: bool = False,
                 message: Optional[str] = None,
                 error_image_path: Optional[str] = None,
                 total_elapsed_seconds: Optional[float] = None):

        self.kind = _Kind(self._KIND_ALIASES.get(str(kind), str(kind)))
        self.image_name = image_name
        self.image_path = image_path
        self.elapsed_seconds = float(elapsed_seconds)
        self.total_elapsed_seconds = float(
            elapsed_seconds if total_elapsed_seconds is None else total_elapsed_seconds
        )
        self.max_wait = max_wait
        self.wait_vanish = bool(wait_vanish)
        self.maintenance_mode = bool(maintenance_mode)
        self.error_image_path = error_image_path
        self.message = message if message is not None else self._default_message()

        # Explicit success flags so a caller can inspect an outcome without
        # knowing the kind constants.
        self.found = False
        self.success = False
        self.succeeded = False

    def _default_message(self) -> str:
        # ASCII only: these messages are printed to consoles that may not
        # support the Windows default code page.
        path = self.image_path or '<unknown>'
        if self.kind == self.TIMEOUT_NOT_FOUND:
            return (f'Image "{self.image_name}" was not found! '
                    f'waited {self.elapsed_seconds:.2f}s. image: {path}')
        if self.kind == self.WAIT_VANISH_TIMEOUT:
            return (f'Image "{self.image_name}" did not disappear after '
                    f'{self.elapsed_seconds:.2f}s. image: {path}')
        return (f'Image "{self.image_name}" was not replaced in maintenance '
                f'mode: the user did not finish the re-capture. '
                f'waited {self.elapsed_seconds:.2f}s. image: {path}')

    # ------------------------------------------------------------------
    # Convenience aliases / predicates
    # ------------------------------------------------------------------
    @property
    def status(self) -> str:
        return self.kind

    @property
    def reason(self) -> str:
        return self.kind

    @property
    def outcome_type(self) -> str:
        return self.kind

    @property
    def elapsed(self) -> float:
        return self.elapsed_seconds

    @property
    def waited_seconds(self) -> float:
        return self.elapsed_seconds

    @property
    def wait_time(self) -> float:
        return self.elapsed_seconds

    @property
    def seconds(self) -> float:
        return self.elapsed_seconds

    @property
    def path(self) -> Optional[str]:
        return self.image_path

    @property
    def image(self) -> Optional[str]:
        return self.image_path

    @property
    def timed_out(self) -> bool:
        return self.kind in (self.TIMEOUT_NOT_FOUND, self.WAIT_VANISH_TIMEOUT)

    @property
    def cancelled(self) -> bool:
        return self.kind == self.MAINTENANCE_ABORTED

    @property
    def aborted(self) -> bool:
        return self.kind == self.MAINTENANCE_ABORTED

    @property
    def not_found(self) -> bool:
        return self.kind == self.TIMEOUT_NOT_FOUND

    @property
    def not_disappeared(self) -> bool:
        return self.kind == self.WAIT_VANISH_TIMEOUT

    @property
    def maintenance_aborted(self) -> bool:
        return self.kind == self.MAINTENANCE_ABORTED

    def is_failure(self) -> bool:
        """Every ``LocatorOutcome`` represents a failure."""
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            'kind': self.kind,
            'image_name': self.image_name,
            'image_path': self.image_path,
            'elapsed_seconds': self.elapsed_seconds,
            'total_elapsed_seconds': self.total_elapsed_seconds,
            'max_wait': self.max_wait,
            'wait_vanish': self.wait_vanish,
            'maintenance_mode': self.maintenance_mode,
            'message': self.message,
            'error_image_path': self.error_image_path,
        }

    def __bool__(self) -> bool:
        # An outcome must never be mistaken for a coordinate or for success.
        return False

    def __iter__(self):
        """Fail loudly if generated code unpacks an outcome as ``(x, y)``.

        Older generated snippets do ``xy = get_coordenadas('x')`` and then
        ``pyautogui.moveTo(*xy)``.  Unpacking an outcome would otherwise fail
        with an obscure TypeError (or, worse, act on wrong coordinates), so we
        raise the same exception the legacy API used.
        """
        from rpapy.core.snipps.snippingtools import (ImageNotDisappearError,
                                                     ImageNotFoundError)
        if self.kind == self.WAIT_VANISH_TIMEOUT:
            error = ImageNotDisappearError(str(self))
        else:
            error = ImageNotFoundError(str(self))
        error.outcome = self
        raise error

    def __eq__(self, other: Any) -> bool:
        # ``outcome == 'timeout'`` is a convenient way to check the kind.
        if isinstance(other, str):
            return self.kind == other
        if isinstance(other, LocatorOutcome):
            return (str(self.kind), self.image_name, self.image_path) == \
                   (str(other.kind), other.image_name, other.image_path)
        return NotImplemented

    def __hash__(self) -> int:
        return hash((str(self.kind), self.image_name, self.image_path))

    def __repr__(self) -> str:
        return (f'<LocatorOutcome kind={self.kind!r} image_name={self.image_name!r} '
                f'image_path={self.image_path!r} elapsed={self.elapsed_seconds:.3f}s>')

    def __str__(self) -> str:
        return self.message


# Common alternative names, exported to make the outcome easy to find.
ImageLocatorOutcome = LocatorOutcome
LocatorResult = LocatorOutcome
LocateResult = LocatorOutcome
ImageSearchOutcome = LocatorOutcome


def is_locator_outcome(value: Any) -> bool:
    """Return True when *value* is a failed lookup outcome (not a coordinate).

    Duck typing is accepted as well so test doubles/custom locators can return
    an equivalent object without importing this module.
    """
    return isinstance(value, LocatorOutcome) or \
        getattr(value, '_is_locator_outcome', False) is True
