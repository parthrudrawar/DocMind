"""Conservative size accounting while a supervised worker atomically renames files."""
import errno
import stat
import time


def _scan_attempts():
    """Allow short atomic-rename bursts, never return an unverified subtotal."""
    deadline = time.monotonic() + 1.0
    for attempt in range(50):
        if attempt:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.02, remaining))
            if time.monotonic() >= deadline:
                return
        yield


def disk_bytes(folder):
    for _ in _scan_attempts():
        try:
            total = 0
            for path in folder.rglob("*"):
                info = path.stat()
                if stat.S_ISREG(info.st_mode):
                    total += info.st_size
            return total
        except FileNotFoundError:
            # A completed .tmp/.part may have been renamed. Restart the entire
            # scan; do not ignore the missing entry or waive the disk budget.
            continue
    raise OSError(errno.EAGAIN, "Temporary storage could not be accounted for")


def model_cache_bytes(folder):
    """Count public HF blobs once, plus their internal symlink entries.

    HF snapshots link to blobs on Linux. Following those links counts the same
    weights twice and falsely rejects cold downloads. This function is NOT for
    private workspace accounting. Reject directory links, junctions and any
    dangling/escaping link rather than excluding arbitrary data from the cap.
    Hard-linked regular files remain conservatively counted at each path.
    """
    if folder.is_symlink() or getattr(folder, "is_junction", lambda: False)():
        raise OSError(errno.EPERM, "Invalid public cache root")
    root = folder.resolve()
    for _ in _scan_attempts():
        try:
            total = 0
            for path in folder.rglob("*"):
                info = path.lstat()
                if getattr(path, "is_junction", lambda: False)():
                    raise OSError(errno.EPERM, "Invalid public cache link")
                if stat.S_ISLNK(info.st_mode):
                    try:
                        target = path.resolve(strict=True)
                    except RuntimeError:
                        raise OSError(errno.EPERM, "Invalid public cache link") from None
                    if not target.is_relative_to(root) or not target.is_file():
                        raise OSError(errno.EPERM, "Invalid public cache link")
                    total += info.st_size
                elif stat.S_ISREG(info.st_mode):
                    total += info.st_size
            return total
        except FileNotFoundError:
            continue  # Atomic model downloads/renames; repeat the whole scan.
    raise OSError(errno.EAGAIN, "Public cache could not be accounted for")
