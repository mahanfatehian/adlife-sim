from enum import IntEnum


class ExitCode(IntEnum):
    SUCCESS = 0
    UNEXPECTED = 1
    INPUT_ERROR = 2
    PROVIDER_ERROR = 3
    ARTIFACT_ERROR = 4
    INTERRUPTED = 130
