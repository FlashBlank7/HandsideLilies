from __future__ import annotations

import sys


def _run_native_capture_helper_if_requested() -> int | None:
    arguments = list(sys.argv[1:])
    if "--native-capture-helper" not in arguments:
        return None
    # Keep the isolated capture path ahead of lilies.app: importing the full
    # Qt/backend graph would consume most of the helper's whole-process
    # deadline before WM_PRINT even starts.
    from lilies.core.native_capture_helper import native_capture_helper_main

    return native_capture_helper_main(arguments)


if __name__ == "__main__":
    helper_result = _run_native_capture_helper_if_requested()
    if helper_result is not None:
        raise SystemExit(helper_result)
    from lilies.app import main

    raise SystemExit(main())
