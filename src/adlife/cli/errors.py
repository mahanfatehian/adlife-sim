import functools
import sys
from collections.abc import Callable
from enum import IntEnum
from typing import Any


class ExitCode(IntEnum):
    SUCCESS = 0
    UNEXPECTED = 1
    INPUT_ERROR = 2
    PROVIDER_ERROR = 3
    ARTIFACT_ERROR = 4
    INTERRUPTED = 130


class CommandError(Exception):
    """A CLI-known failure carrying its exit code and a safe message.

    The message may name a file, a run identifier or a field; it may never echo a
    credential or an artifact's contents, which is why commands raise this instead of
    letting raw exceptions reach the boundary.
    """

    def __init__(self, message: str, exit_code: int = ExitCode.INPUT_ERROR) -> None:
        super().__init__(message)
        self.exit_code = int(exit_code)


_CURRENT_FORMAT = "human"


def set_output_format(fmt: str) -> None:
    """Record the active global ``--format``; the root callback calls this once."""
    global _CURRENT_FORMAT
    _CURRENT_FORMAT = fmt if fmt in {"human", "json", "jsonl"} else "human"


def output_format() -> str:
    """The active global ``--format``, or ``human`` outside a command's run."""
    return _CURRENT_FORMAT


def _debug_requested() -> bool:
    """Whether the operator explicitly asked for diagnostic detail (``ADLIFE_DEBUG=1``)."""
    import os

    return os.environ.get("ADLIFE_DEBUG", "") == "1"


def _report(
    message: str,
    exit_code: int,
    error_type: str,
    *,
    debug: bool = False,
) -> None:
    """Report a boundary failure in the active output mode.

    JSON mode prints ONE machine-readable error object on stdout and the diagnostic on
    stderr; human mode prints the concise message on stderr, where it cannot corrupt a
    pipe. A Python traceback accompanies the diagnostic only when the failure is an
    unexpected internal defect or the operator set ``ADLIFE_DEBUG=1``: an expected
    failure is a normal conversation with the user, not a stack walk.
    """
    import traceback

    if output_format() in {"json", "jsonl"}:
        import json

        document = {
            "error": {
                "exit_code": exit_code,
                "type": error_type,
                "message": message,
            }
        }
        # ONE machine-readable object on stdout; the human diagnostic goes to stderr.
        print(json.dumps(document, ensure_ascii=False), flush=True)
        print(f"error: {message}", file=sys.stderr)
    else:
        print(f"error: {message}", file=sys.stderr)
    # The stack walk is the only conditional part: it serves an unexpected defect or an
    # explicit ADLIFE_DEBUG=1 request, never ordinary usage of a working tool.
    if debug:
        traceback.print_exc(file=sys.stderr)


def command_boundary(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Map the documented domain exceptions to the documented exit codes.

    Validation and input errors exit 2, provider configuration errors 3, corrupted
    artifacts 4, interruption 130, and anything unrecognized 1. ``typer.Exit`` and
    ``click``'s own control flow pass through untouched, and ``KeyboardInterrupt`` is
    re-raised so the process can report 130 through ``main``.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        import typer
        import yaml
        from pydantic import ValidationError

        from adlife.adapters.cognition.cache import CacheMiss
        from adlife.adapters.cognition.openai_compatible import ProviderConfigurationError
        from adlife.core.ports.cognition import CognitionError
        from adlife.core.ports.run_store import StorageError

        debug = _debug_requested()
        try:
            return fn(*args, **kwargs)
        except KeyboardInterrupt:
            raise
        except (typer.Exit, SystemExit):
            raise
        except CommandError as error:
            _report(str(error), error.exit_code, type(error).__name__, debug=debug)
            raise SystemExit(error.exit_code) from None
        except (ValidationError, ValueError, yaml.YAMLError) as error:
            _report(str(error), ExitCode.INPUT_ERROR, type(error).__name__, debug=debug)
            raise SystemExit(ExitCode.INPUT_ERROR) from None
        except (CognitionError, CacheMiss, ProviderConfigurationError) as error:
            _report(str(error), ExitCode.PROVIDER_ERROR, type(error).__name__, debug=debug)
            raise SystemExit(ExitCode.PROVIDER_ERROR) from None
        except StorageError as error:
            _report(str(error), ExitCode.ARTIFACT_ERROR, type(error).__name__, debug=debug)
            raise SystemExit(ExitCode.ARTIFACT_ERROR) from None
        except Exception as error:
            # An unexpected defect is the one failure a traceback always serves: it is
            # a bug, and the stack is the bug report. ADLIFE_DEBUG=1 adds the same
            # detail to expected failures.
            _report(str(error), ExitCode.UNEXPECTED, type(error).__name__, debug=True)
            raise SystemExit(ExitCode.UNEXPECTED) from None

    return wrapper


__all__ = [
    "CommandError",
    "ExitCode",
    "command_boundary",
    "output_format",
    "set_output_format",
]
