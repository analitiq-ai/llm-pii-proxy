"""Child processes the proxy runs: gitleaks and pdftoppm."""

import asyncio


async def kill_process(process: asyncio.subprocess.Process) -> None:
    """Ends `process` if it is still running, and reaps it."""
    if process.returncode is None:
        process.kill()
    await process.wait()
