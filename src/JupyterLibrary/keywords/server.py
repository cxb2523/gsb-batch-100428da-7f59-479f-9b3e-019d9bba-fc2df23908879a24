"""A library component for managing Jupyter servers."""
import contextlib
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import typing
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen
from uuid import uuid4

from robot.libraries.BuiltIn import BuiltIn
from SeleniumLibrary.base import LibraryComponent, keyword

#: Fragments that a server process may print when it cannot bind its port.
#: These are used to tell a recoverable port race apart from a real failure.
PORT_CONFLICT_MARKERS = (
    "address already in use",
    "could not be started because port",
    "eaddrinuse",
    "error while attempting to bind on address",
    "is already in use",
    "only one usage of each socket address",
)

#: The URL a server prints once it is listening, e.g. ``http://127.0.0.1:8888/x``.
_SERVER_URL = re.compile(
    r"https?://(?:127\.0\.0\.1|localhost|0\.0\.0\.0|\[::1\]):(\d+)(?=[/:\s]|$)",
)

#: Text that introduces the listening URL(s) in a Jupyter server log.
_RUNNING_AT = re.compile(r"(?:is|now) running at:?", re.IGNORECASE)


class ServerKeywords(LibraryComponent):

    """A component that extends the core with Jupyter server management."""

    _handles: typing.List[subprocess.Popen]
    _tmpdirs: typing.Dict[subprocess.Popen, str]
    _notebook_dirs: typing.Dict[subprocess.Popen, str]
    _ports: typing.Dict[subprocess.Popen, int]
    _base_urls: typing.Dict[subprocess.Popen, str]
    _tokens: typing.Dict[subprocess.Popen, str]
    _port_candidates: typing.List[int]
    _app_name: typing.Optional[str]

    def __init__(self, *args, **kwargs):
        """Initialize a library with jupyter server keywords."""
        super().__init__(*args, **kwargs)
        self._handles = []
        self._tmpdirs = {}
        self._notebook_dirs = {}
        self._ports = {}
        self._base_urls = {}
        self._tokens = {}
        self._port_candidates = []
        self._app_name = None

    @keyword
    def set_default_jupyter_app_name(
        self,
        app_name: typing.Optional[str] = None,
    ) -> typing.Optional[str]:
        """Set the current ``traitlets.Configurable`` Jupyter Server application, returning the previous value.

        A value of ``None`` (the default) will try to detect the app based on
        command name, such as:

        - ``jupyter-notebook`` is configured with ``NotebookApp``
        - ``jupyter-lab`` and most other are configured with ``ServerApp``

        This may also be set externally via the ``JUPYTER_LIBRARY_APP`` environment
        variable, but any explicit argument will override this.

        See [#Get Jupyter App Name] for more.
        """
        old_app_name = self.app_name
        self.app_name = app_name
        return old_app_name

    @keyword
    def get_jupyter_app_name(
        self,
        command: typing.Optional[str] = None,
    ) -> typing.Optional[str]:
        """Get the current ``traitlets.Configurable`` Jupyter Server application, optionally for a specific CLI command.

        See [#Set Default Jupyter App Name] for more.
        """
        app_name = os.environ.get("JUPYTER_LIBRARY_APP")

        if self._app_name is not None:
            app_name = self._app_name
        elif command is not None and "jupyter-notebook" in command:
            app_name = "NotebookApp"

        return app_name or "ServerApp"

    @keyword
    def start_new_jupyter_server(
        self,
        command: typing.Optional[str] = "jupyter-notebook",
        port: typing.Optional[int] = None,
        base_url: typing.Optional[str] = "/@rf/",
        notebook_dir: typing.Optional[str] = None,
        token: typing.Optional[str] = None,
        *args,
        **config,
    ) -> subprocess.Popen:
        """Start a Jupyter server. All arguments are optional.

        | = argument =     | = default =           | = notes =                               |
        | ``command``      | ``jupyter-notebook``  | e.g. ``jupyter-lab``                    |
        | ``port``         | an unused port        |                                         |
        | ``base_url``     | ``/@rf/``             |                                         |
        | ``notebook_dir`` | a temporary directory |                                         |
        | ``token``        | a random ``uuid4``    |                                         |
        | ``*args``        |                       | extra server arguments                  |
        | ``**config``     |                       | extra process arguments                 |
        | ``app_name``     | ``None`` (detect)     | e.g. ``NotebookApp``, `ServerApp``      |
        | ``extra_args``   | ``[]``                | extra arguments beyond ```token``, etc. |
        | ``port_retries`` | ``2``                 | retries on a lost port race             |
        | ``start_timeout``| ``15``                | seconds to watch the startup            |
        | ``start_interval``| ``0.1``              | seconds between startup checks          |

        If not configured, the ``%{HOME}`` environment variable and current
        working directory will be set to avoid leaking configuration
        between between runs (or the test instance) itself. These
        directories will be cleaned up after the server process is
        [#Terminate All Jupyter Servers|terminated].

        The ``app_name`` argument is as described for the [#Get Jupyter App Name|app name],
        with the default being to autodetect from the command and environment.

        ``extra_args`` are passed to ``Start Process`` before the ``token``

        When ``port`` is not given, an unused port is chosen with
        [#Get Unused Port]. Because another process can claim that port
        between the probe and the actual bind, a server that dies during
        startup because its port is unavailable is automatically restarted
        on a fresh port. The number of attempts is controlled by
        ``port_retries`` (default ``2``, meaning up to three attempts), and
        the port, URL and token finally in use are recorded against the
        returned handle, so [#Get Jupyter Server URL],
        [#Get Jupyter Server Token] and [#Shut Down Jupyter Server] keep
        working for the restarted server. [#Get Jupyter Server Port] reports
        the port that was actually bound.

        The server's own ``--port_retries`` is set to ``0``, so a taken port
        makes the server exit instead of silently moving somewhere the
        library cannot see. The library then owns the retry, and can record
        the port it really used.

        If ``port`` **is** given explicitly it is never changed: a startup
        failure caused by that port raises an ``RuntimeError`` describing the
        port and the reason it could not be used.

        When every attempt is exhausted, an ``RuntimeError`` is raised that
        lists the ports that were tried and the last failure. A handle that
        never became usable is never returned. A process that fails for any
        reason other than a port conflict is reported immediately, without
        retrying, as retrying cannot help it.

        The startup watch can be tuned with ``start_timeout`` (seconds,
        default ``15``) and ``start_interval`` (seconds, default ``0.1``).
        A server still running when ``start_timeout`` elapses is treated as
        started, leaving the final readiness check to
        [#Wait For Jupyter Server To Be Ready].
        """
        app_name = (
            config.pop("app_name", None)
            or config.get("env:JUPYTER_LIBRARY_APP")
            or self.get_jupyter_app_name(command)
        )
        explicit_port = port is not None
        port_retries = max(0, int(config.pop("port_retries", 2)))
        start_timeout = float(config.pop("start_timeout", 15))
        start_interval = float(config.pop("start_interval", 0.1))

        token = str(uuid4()) if token is None else token

        BuiltIn().import_library("Process")
        plib = BuiltIn().get_library_instance("Process")

        extra_args = config.pop("extra_args", [])

        # When the caller supplies the server arguments, the library neither
        # owns the ``--port`` flag nor can it inject port behaviour, so there
        # is nothing safe to retry.
        caller_owns_arguments = bool(args)
        candidates = self._get_port_candidates()

        if explicit_port:
            attempts: typing.Iterable[int] = [port]
        elif caller_owns_arguments:
            attempts = candidates[:1] if candidates else [self.get_unused_port()]
        elif candidates:
            attempts = self._unique_ports(candidates)[: port_retries + 1]
        else:
            attempts = self._probe_ports(port_retries + 1)

        failures: typing.List[typing.Dict[str, typing.Any]] = []
        attempted_ports: typing.List[int] = []

        for attempt_port in attempts:
            attempted_ports += [attempt_port]
            attempt = self._start_jupyter_server_attempt(
                plib,
                command,
                port=attempt_port,
                base_url=base_url,
                notebook_dir=notebook_dir,
                token=token,
                app_name=app_name,
                args=args,
                extra_args=extra_args,
                config=config,
                start_timeout=start_timeout,
                start_interval=start_interval,
                caller_owns_arguments=caller_owns_arguments,
            )

            if attempt["status"] == "ready":
                handle = attempt["handle"]
                actual_port = attempt["port"]
                self._handles += [handle]
                self._tmpdirs[handle] = str(attempt["tmp_path"])
                self._notebook_dirs[handle] = str(attempt["notebook_dir"])
                self._ports[handle] = actual_port
                self._base_urls[handle] = base_url
                self._tokens[handle] = token
                return handle

            failures += [{"port": attempt_port, "reason": attempt["detail"]}]
            self._discard_jupyter_server_attempt(
                plib,
                attempt["handle"],
                attempt["tmp_path"],
            )

            if attempt["status"] != "port_conflict":
                raise RuntimeError(
                    self._start_failure_message(
                        command,
                        attempted_ports,
                        failures,
                        explicit_port,
                    ),
                )

            if explicit_port:
                message = (
                    f"Could not start Jupyter server '{command}' on port "
                    f"{attempt_port}: {attempt['detail']}\n"
                    "The port was given explicitly, so it will not be changed "
                    "automatically. Pass no port to allow retrying on another "
                    "port."
                )
                raise RuntimeError(message)

            if caller_owns_arguments:
                message = (
                    f"Could not start Jupyter server '{command}' on port "
                    f"{attempt_port}: {attempt['detail']}\n"
                    "The server arguments were supplied explicitly, so the "
                    "library cannot retry with a different port. Pass no "
                    "*args to allow retrying on another port."
                )
                raise RuntimeError(message)

        raise RuntimeError(
            self._start_failure_message(
                command,
                attempted_ports,
                failures,
                explicit_port,
            ),
        )

    def _get_port_candidates(self) -> typing.Optional[typing.List[int]]:
        """Return the explicitly configured candidate ports, if any."""
        return list(self._port_candidates) if self._port_candidates else None

    def _probe_ports(self, count: int) -> typing.Iterator[int]:
        """Probe a fresh unused port for each attempt.

        Probing lazily (instead of up front) gives every retry a chance at a
        port that was not free when the retries were planned.
        """
        seen: typing.List[int] = []
        while len(seen) < count:
            port = self.get_unused_port()
            if port in seen:
                continue
            seen += [port]
            yield port

    @staticmethod
    def _unique_ports(ports: typing.Iterable[int]) -> typing.List[int]:
        """Keep the first occurrence of each port, preserving order."""
        unique: typing.List[int] = []
        for port in ports:
            if port not in unique:
                unique += [port]
        return unique

    def _start_jupyter_server_attempt(
        self,
        plib,
        command: str,
        *,
        port: int,
        base_url: str,
        notebook_dir: typing.Optional[str],
        token: str,
        app_name: str,
        args: typing.Tuple[str, ...],
        extra_args: typing.List[str],
        config: typing.Dict[str, typing.Any],
        start_timeout: float,
        start_interval: float,
        caller_owns_arguments: bool,
    ) -> typing.Dict[str, typing.Any]:
        """Start one server process and watch it through its startup phase."""
        tmp_path = Path(tempfile.mkdtemp())
        attempt_config = dict(config)

        if "env:HOME" not in attempt_config:
            home_dir = tmp_path / "home"
            home_dir.mkdir()
            attempt_config["env:HOME"] = str(home_dir)

        if "stdout" not in attempt_config:
            attempt_config["stdout"] = str(tmp_path / "server.log")

        if "stderr" not in attempt_config:
            attempt_config["stderr"] = "STDOUT"

        attempt_notebook_dir = notebook_dir
        if attempt_notebook_dir is None:
            attempt_notebook_dir = str(tmp_path / "notebooks")
            Path(attempt_notebook_dir).mkdir()
            attempt_config["cwd"] = attempt_notebook_dir

        if args:
            attempt_args = args
        else:
            attempt_args = self.build_jupyter_server_arguments(
                port,
                base_url,
                token,
                app_name,
            )
            # Jupyter silently moves to the next port when one is taken. That
            # would make the recorded URL wrong without any way to notice, so
            # binding failures are made fatal and handled (and retried) here.
            attempt_args = (*attempt_args, f"--{app_name}.port_retries=0")

        log_path = self._get_attempt_log_path(attempt_config)

        try:
            handle = plib.start_process(
                command,
                *extra_args,
                *attempt_args,
                **attempt_config,
            )
        except Exception:
            shutil.rmtree(tmp_path, ignore_errors=True)
            raise

        status, detail, actual_port = self._await_jupyter_server_start(
            plib,
            handle,
            port=port,
            base_url=base_url,
            log_path=log_path,
            start_timeout=start_timeout,
            start_interval=start_interval,
            accept_any_port=caller_owns_arguments,
        )

        return {
            "handle": handle,
            "tmp_path": tmp_path,
            "notebook_dir": attempt_notebook_dir,
            "status": status,
            "detail": detail,
            "port": actual_port,
        }

    def _get_attempt_log_path(
        self,
        config: typing.Dict[str, typing.Any],
    ) -> typing.Optional[Path]:
        """Get the path of the log written by a started attempt, if it is a file."""
        stdout = config.get("stdout")
        if not isinstance(stdout, str) or stdout.upper() in {"STDOUT", "DEVNULL"}:
            return None
        cwd = config.get("cwd")
        cwd_path = Path(cwd) if cwd else Path.cwd()
        return (cwd_path / stdout).resolve()

    def _await_jupyter_server_start(
        self,
        plib,
        handle: subprocess.Popen,
        *,
        port: int,
        base_url: str,
        log_path: typing.Optional[Path],
        start_timeout: float,
        start_interval: float,
        accept_any_port: bool,
    ) -> typing.Tuple[str, typing.Optional[str], int]:
        """Watch a starting server and report how far it got.

        The status is ``ready``, ``port_conflict`` or ``exited``; the third
        value is the port the server actually bound, which can only differ
        from ``port`` when ``accept_any_port`` is set.
        """
        deadline = time.time() + max(0.0, start_timeout)

        while True:
            status, detail, actual_port = self._check_start_state(
                plib,
                handle,
                port=port,
                base_url=base_url,
                log_path=log_path,
                accept_any_port=accept_any_port,
            )

            if status == "pending":
                if time.time() >= deadline:
                    # Slow but still alive: leave readiness to the dedicated
                    # keyword, exactly like the historical behaviour.
                    return "ready", None, port
                time.sleep(start_interval)
                continue

            return status, detail, actual_port

    def _check_start_state(
        self,
        plib,
        handle: subprocess.Popen,
        *,
        port: int,
        base_url: str,
        log_path: typing.Optional[Path],
        accept_any_port: bool,
    ) -> typing.Tuple[str, typing.Optional[str], int]:
        """Report the state of a starting server: how far it got, and on what port."""
        running = self._process_is_running(handle)
        output = self._read_log_tail(log_path)
        conflict = self._find_port_conflict(output)

        if not running and not output:
            output = self._get_finished_process_output(plib, handle)
            conflict = conflict or self._find_port_conflict(output)

        # A listening URL wins over an earlier complaint: a caller that owns
        # the arguments may have let the server move ports, and the server
        # then says where it really is.
        running_ports = self._find_running_ports(output)
        if running_ports:
            return self._classify_running_ports(
                running_ports,
                port,
                accept_any_port,
            )

        # A port conflict must never be reported while the process is alive:
        # a server that is mid-shutdown also prints one, and a slow server may
        # still be starting up after an earlier complaint.
        if conflict:
            if self._process_is_running(handle):
                self._wait_briefly_for_exit(handle)
                if self._process_is_running(handle):
                    return "pending", None, port
            return "port_conflict", conflict, port

        if not running:
            reason = self._describe_failed_attempt(plib, handle, port, output)
            return "exited", reason, port

        # Without a log there is nothing to parse, so fall back to asking the
        # port. With a log, an empty one means 'still starting': probing could
        # mistake an unrelated process that holds the port for our server.
        if log_path is None and self._probe_server_url(port, base_url):
            return "ready", None, port

        return "pending", None, port

    @staticmethod
    def _classify_running_ports(
        running_ports: typing.List[int],
        port: int,
        accept_any_port: bool,
    ) -> typing.Tuple[str, typing.Optional[str], int]:
        """Decide whether the port a server reports matches the one requested."""
        if port in running_ports:
            return "ready", None, port
        if accept_any_port:
            return "ready", None, running_ports[0]
        detail = (
            "the server reported it was running on port "
            f"{running_ports[0]} instead of the requested port {port}"
        )
        return "port_conflict", detail, port

    @staticmethod
    def _process_is_running(handle: subprocess.Popen) -> bool:
        """Check a process without depending on the Process library instance."""
        return handle.poll() is None

    @staticmethod
    def _wait_briefly_for_exit(handle: subprocess.Popen) -> None:
        """Give a process that reported a port conflict a moment to exit."""
        with contextlib.suppress(subprocess.TimeoutExpired):
            handle.wait(timeout=5)

    @staticmethod
    def _find_running_ports(output: str) -> typing.List[int]:
        """Find the ports of URLs a server prints once it is listening.

        Jupyter reports the URLs on the lines after its ``... is running at:``
        message, and repeats them once per local address.
        """
        if not output:
            return []
        ports: typing.List[int] = []
        reported = False
        for line in output.splitlines():
            if _RUNNING_AT.search(line):
                reported = True
                ports += [int(port) for port in _SERVER_URL.findall(line)]
            elif reported:
                ports += [int(port) for port in _SERVER_URL.findall(line)]
        return sorted(set(ports))

    @staticmethod
    def _probe_server_url(port: int, base_url: str) -> bool:
        """Check whether something answers HTTP on a port yet."""
        url = f"http://127.0.0.1:{port}{base_url or '/'}"
        try:
            urlopen(url, timeout=1)
        except HTTPError:
            # An authentication or not-found response still proves the bind.
            return True
        except Exception:
            return False
        return True

    @staticmethod
    def _read_log_tail(
        log_path: typing.Optional[Path],
        max_bytes: int = 20000,
    ) -> str:
        """Read the end of an attempt's log without raising.

        ``Process`` truncates the ``stdout`` file when it starts a process, so
        the file only ever holds output of the attempt currently being watched.
        """
        if log_path is None or not log_path.is_file():
            return ""
        try:
            with log_path.open("rb") as log_file:
                log_file.seek(0, os.SEEK_END)
                size = log_file.tell()
                log_file.seek(max(0, size - max_bytes))
                return log_file.read().decode("utf-8", errors="replace")
        except OSError:
            return ""

    @staticmethod
    def _find_port_conflict(output: str) -> typing.Optional[str]:
        """Return the log line that shows a port conflict, if any."""
        if not output:
            return None
        for line in output.splitlines():
            if any(marker in line.lower() for marker in PORT_CONFLICT_MARKERS):
                return line.strip()
        return None

    def _describe_failed_attempt(
        self,
        plib,
        handle: subprocess.Popen,
        port: int,
        output: str,
    ) -> str:
        """Build a human-readable reason for a server that died during startup."""
        return_code = handle.poll()
        if not output:
            output = self._get_finished_process_output(plib, handle)
        reason = f"port {port} could not be used"
        if return_code is not None:
            reason = f"the server process exited with code {return_code}"
        tail = self._tail(output)
        if tail:
            return f"{reason}. Server output:\n{tail}"
        return reason

    @staticmethod
    def _get_finished_process_output(plib, handle: subprocess.Popen) -> str:
        """Best-effort retrieval of output for a process that has already exited."""
        try:
            plib.wait_for_process(handle, timeout="1s")
            return plib.get_process_result(handle, stdout=True, stderr=True)[0]
        except Exception:
            return ""

    @staticmethod
    def _tail(output: str, lines: int = 40, chars: int = 4000) -> str:
        """Take a readable tail of a log for use in an error message."""
        if not output:
            return ""
        tail = "\n".join(output.strip().splitlines()[-lines:])
        return tail[-chars:]

    def _discard_jupyter_server_attempt(
        self,
        plib,
        handle: subprocess.Popen,
        tmp_path: Path,
    ) -> None:
        """Stop and remove the files of a startup attempt that will not be used."""
        try:
            if self._process_is_running(handle):
                plib.terminate_process(handle, kill=True)
            else:
                plib.wait_for_process(handle, timeout="1s")
        except Exception as err:
            BuiltIn().log(f"Ignoring error while cleaning up unused server: {err}")
        shutil.rmtree(tmp_path, ignore_errors=True)

    @staticmethod
    def _start_failure_message(
        command: str,
        attempted_ports: typing.List[int],
        failures: typing.List[typing.Dict[str, typing.Any]],
        explicit_port: bool,
    ) -> str:
        """Explain which ports were tried and why the last attempt failed."""
        ports = ", ".join(str(port) for port in attempted_ports) or "none"
        last = failures[-1]["reason"] if failures else "no attempt was made"
        last_port = failures[-1]["port"] if failures else None
        port_kind = "explicit" if explicit_port else "automatically selected"
        attempts = len(failures)

        message = (
            f"Failed to start Jupyter server '{command}' after {attempts} "
            f"attempt(s) using {port_kind} port(s): {ports}. "
        )
        if last_port is not None:
            message += f"Last failure on port {last_port}: {last}"
        else:
            message += f"Last failure: {last}"
        return message

    @keyword
    def set_jupyter_server_port_candidates(self, *ports) -> typing.List[int]:
        """Set the ports used by [#Start New Jupyter Server] instead of probing.

        The ports are tried in order, and at most ``port_retries + 1`` of them
        are used. This makes port-conflict behaviour predictable, which is
        mostly useful when testing this library (or when a few known ports
        should be preferred). Pass no ports to restore automatic selection.

        Returns the previously configured list.
        """
        previous = list(self._port_candidates)
        self._port_candidates = [int(port) for port in ports]
        return previous

    @keyword
    def build_jupyter_server_arguments(
        self,
        port: int,
        base_url: str,
        token: str,
        app_name: typing.Optional[str] = None,
    ) -> typing.Tuple[str]:
        """Build Some default Jupyter application arguments.

        If the ``app_name`` is not provided, it will be detected based on the rules
        in [#Get Jupyter App Name].
        """
        app_name = app_name or self.get_jupyter_app_name()
        return (
            "--no-browser",
            "--debug",
            f"--port={port}",
            f"--{app_name}.token={token}",
            f"--{app_name}.base_url={base_url}",
        )

    @keyword
    def copy_files_to_jupyter_directory(self, *sources: str, **kwargs) -> None:
        """Copy some files into the (temporary) jupyter server root.

        | = argument = | = default =                       |
        | ``nbserver`` | the most-recently launched server |
        """
        nbserver = kwargs.get("nbserver", self._handles[-1])
        notebook_dir = self._notebook_dirs[nbserver]
        BuiltIn().import_library("OperatingSystem")
        osli = BuiltIn().get_library_instance("OperatingSystem")
        return osli.copy_files(*sorted(sources), notebook_dir)

    @keyword
    def copy_files_from_jupyter_directory(self, *src_and_dest: str, **kwargs) -> None:
        """Copy some files from the (temporary) jupyter server root.

        | = argument = | = default =                       |
        | ``nbserver`` | the most-recently launched server |

        Patterns will have the notebook directory prepended
        """
        nbserver = kwargs.get("nbserver", self._handles[-1])
        notebook_dir = Path(self._notebook_dirs[nbserver])
        BuiltIn().import_library("OperatingSystem")
        osli = BuiltIn().get_library_instance("OperatingSystem")
        sources = [str(notebook_dir / src) for src in src_and_dest[:-1]]
        dest = src_and_dest[-1]
        return osli.copy_files(*sources, dest)

    @keyword
    def get_jupyter_directory(
        self,
        nbserver: typing.Optional[subprocess.Popen] = None,
    ) -> str:
        """Get the Jupyter contents directory.

        | = argument = | = default =                       |
        | ``nbserver`` | the most-recently launched server |
        """
        nbserver = nbserver if nbserver is not None else self._handles[-1]

        return self._notebook_dirs[nbserver]

    @keyword
    def wait_for_jupyter_server_to_be_ready(
        self,
        *nbservers: subprocess.Popen,
        **kwargs,
    ) -> int:
        """Wait for the most-recently started Jupyter server to be ready.

        A server whose process has stopped is reported immediately, with the
        port it was meant to use and the tail of its output, instead of
        waiting for the whole ``retries``/``interval`` budget and raising an
        error that hides the cause.
        """
        interval = float(kwargs.get("interval", 0.5))
        retries = int(kwargs.get("retries", 60))

        if not nbservers:
            if not self._handles:
                return 0
            nbservers = [self._handles[-1]]

        ready = 0
        error = None

        while retries and ready != len(nbservers):
            retries -= 1
            ready = 0
            try:
                for nbh in nbservers:
                    urlopen(self.get_jupyter_server_url(nbh))
                    ready += 1
                continue
            except Exception as _error:
                error = _error

            stopped = [nbh for nbh in nbservers if not self._process_is_running(nbh)]
            if stopped:
                raise RuntimeError(
                    "\n".join(self._stopped_server_message(nbh) for nbh in stopped),
                ) from error

            time.sleep(interval)

        if ready != len(nbservers):
            message = (
                f"Only {ready} of {len(nbservers)} servers were ready after "
                f"{interval * retries}s. Last error: {type(error)} {error}"
            )
            raise RuntimeError(message)
        return ready

    def _stopped_server_message(self, nbh: subprocess.Popen) -> str:
        """Explain why a server that has stopped can never become ready."""
        details = self._ports.get(nbh)
        if details is None:
            return (
                f"Jupyter server process exited (code {nbh.poll()}) before it "
                "became ready; its URL details had already been released. "
                "Check the server log for the reason."
            )
        return (
            f"Jupyter server process exited (code {nbh.poll()}) before it "
            f"became ready; it was expected on port {details} "
            f"(http://127.0.0.1:{details}{self._base_urls[nbh]}). Check the "
            "server log for the reason, e.g. a port conflict or a missing "
            "Jupyter command."
        )

    @keyword
    def get_jupyter_server_port(
        self,
        nbserver: typing.Optional[subprocess.Popen] = None,
    ) -> int:
        """Get the port the given (or most recently-launched) server is using.

        This is the port the server actually bound, which differs from a
        requested port when a startup port race forced a restart on a fresh
        port. See [#Start New Jupyter Server|Start New Jupyter Server].
        """
        nbh = nbserver or self._handles[-1]
        return self._ports[nbh]

    @keyword
    def get_jupyter_server_url(
        self,
        nbserver: typing.Optional[subprocess.Popen] = None,
    ) -> str:
        """Get the given (or most recently-launched) server's URL."""
        nbh = nbserver or self._handles[-1]
        return f"http://127.0.0.1:{self._ports[nbh]}{self._base_urls[nbh]}"

    @keyword
    def get_jupyter_server_token(
        self,
        nbserver: typing.Optional[subprocess.Popen] = None,
    ) -> str:
        """Get the given (or most recently-launched) server's token."""
        nbh = nbserver or self._handles[-1]
        return self._tokens[nbh]

    @keyword
    def wait_for_new_jupyter_server_to_be_ready(
        self,
        command: typing.Optional[str] = None,
        *args,
        **config,
    ) -> subprocess.Popen:
        """Get the given (or most recently-launched) server's token.

        See [#Start New Jupyter Server|Start New Jupyter Server].
        """
        handle = self.start_new_jupyter_server(command, *args, **config)
        self.wait_for_jupyter_server_to_be_ready(handle)
        return handle

    @keyword
    def shut_down_jupyter_server(self, nbserver=None) -> int:
        """Gracefully shut down a Jupyter server started by [#Start New Jupyter Server|Start New Jupyter Server].

        If no ``handle`` is given, the last-started server will be shut down.
        """
        nbh = nbserver or self._handles[-1]
        url = self.get_jupyter_server_url(nbh)
        token = self.get_jupyter_server_token(nbh)

        try:
            urlopen(f"{url}api/shutdown?token={token}", data=[])
        except Exception as err:
            BuiltIn().log(err)
            return 0

        self._forget_port_details(nbh)

        return 1

    def _forget_port_details(self, nbh) -> None:
        """Drop the URL-related details of a handle, keeping other bookkeeping."""
        self._ports.pop(nbh, None)
        self._base_urls.pop(nbh, None)
        self._tokens.pop(nbh, None)

    @keyword
    def clean_up_jupyter_server_files(self, nbserver=None) -> int:
        """Clean up the files owned by a started by [#Start New Jupyter Server|Jupyter Server].

        If no ``handle`` is given, the last-started server will be terminated.
        """
        nbh = nbserver or self._handles[-1]

        shutil.rmtree(self._tmpdirs[nbh], ignore_errors=True)

        self._tmpdirs.pop(nbh)

    @keyword
    def terminate_jupyter_server(self, nbserver=None) -> int:
        """Close a Jupyter server started by [#Start New Jupyter Server|Start New Jupyter Server].

        If no ``nbserver`` is given, the last-started server will be terminated.

        Waiting ``timeout`` to ensure all files/processes are freed before
        cleaning up temporary directories, if any.
        """
        plib = BuiltIn().get_library_instance("Process")

        nbh = nbserver or self._handles[-1]

        plib.terminate_process(nbh)

        self._handles.remove(nbh)

        # The port may already have been released by a shut down or a failed
        # stop, and ``Terminate All Jupyter Servers`` relies on this being safe.
        self._forget_port_details(nbh)

        return 1

    @keyword
    def terminate_all_jupyter_servers(self, timeout: str = "6s") -> int:
        """Close all Jupyter servers started by [#Start New Jupyter Server|Start New Jupyter Server].

        Waiting ``timeout`` after termination to ensure all files/processes are freed before
        cleaning up temporary directories.
        """
        was_shutdown = []
        was_terminated = []
        for nbh in self._handles:
            try:
                self.shut_down_jupyter_server(nbh)
                was_shutdown += [nbh]
            except Exception as err:
                BuiltIn().log(err)

        handles = list(self._handles)

        for nbh in handles:
            try:
                self.terminate_jupyter_server(nbh)
                was_terminated += [nbh]
            except Exception as err:
                BuiltIn().log(err)

        if was_shutdown or was_terminated:
            BuiltIn().sleep(timeout)

        tmpdir_handles = list(self._tmpdirs)

        for nbh in tmpdir_handles:
            self.clean_up_jupyter_server_files(nbh)

        return len(was_terminated)

    @keyword
    def get_unused_port(self) -> int:
        """Find an unused network port (could still create race conditions)."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("localhost", 0))
        s.listen(1)
        port = s.getsockname()[1]
        s.close()
        return port
