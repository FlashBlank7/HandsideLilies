from __future__ import annotations

import sys


def _run_native_capture_helper_if_requested() -> int | None:
    arguments = list(sys.argv[1:])
    if "--native-capture-helper" not in arguments:
        return None
    from .core.native_capture_helper import native_capture_helper_main

    return native_capture_helper_main(arguments)


if __name__ == "__main__":
    helper_result = _run_native_capture_helper_if_requested()
    if helper_result is not None:
        raise SystemExit(helper_result)
    from .app import main

    raise SystemExit(main())
