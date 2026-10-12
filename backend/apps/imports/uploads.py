"""Where an uploaded CSV waits until the worker has processed it (issue #62).

* One file per batch, named after the batch id (``<uuid4>.csv``): random, never derived from the
  user's file name, so a hostile name cannot reach the file system (the original name is only
  stored on the batch, reduced to its base name).
* A dedicated ``FileSystemStorage`` rooted at ``settings.IMPORT_UPLOAD_DIR`` with no served URL:
  nothing here is ever served over HTTP. Files are mode 0600, the directory 0700.
* The api writes the file, the worker reads it, so both must see the same directory (one shared
  volume in production, see ``docs/environment.md``).
* The file is deleted as soon as its batch ends (completed, partial, failed, cancelled).
  ``purge_stale_uploads`` (``manage.py cleanup_import_uploads``) removes anything left behind:
  files of finished batches, of batches that no longer exist, or older than a cut-off.

Nothing in this module logs file names or contents; only batch ids.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import IO, Any

from django.conf import settings
from django.core.files import File
from django.core.files.storage import FileSystemStorage
from django.utils import timezone

from .models import BATCH_TERMINAL, ImportBatch

logger = logging.getLogger(__name__)

_NAME = re.compile(r"^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.csv$")


class UploadError(OSError):
    """The uploaded file could not be stored."""


def upload_storage() -> FileSystemStorage:
    """The private storage (built per call so a changed setting, e.g. in tests, is honoured)."""
    return FileSystemStorage(
        location=str(settings.IMPORT_UPLOAD_DIR),
        base_url=None,
        file_permissions_mode=0o600,
        directory_permissions_mode=0o700,
    )


def upload_name(batch: ImportBatch | uuid.UUID | str) -> str:
    batch_id = batch.pk if isinstance(batch, ImportBatch) else batch
    return f"{uuid.UUID(str(batch_id))}.csv"


def save_upload(batch: ImportBatch, source: IO[bytes] | Any) -> int:
    """Copy ``source`` (positioned anywhere; it is rewound first) into the batch's file and return
    the number of bytes written."""
    storage = upload_storage()
    name = upload_name(batch)
    if storage.exists(name):  # a retried request: never keep two files for one batch
        storage.delete(name)
    if hasattr(source, "seek"):
        source.seek(0)
    saved = storage.save(name, File(source))
    if saved != name:  # pragma: no cover - the name is a fresh uuid
        storage.delete(saved)
        raise UploadError("The upload file name is already taken.")
    return int(storage.size(name))


def open_upload(batch: ImportBatch) -> IO[bytes]:
    """The stored file, open for binary reading. Raises ``FileNotFoundError`` if it is gone."""
    return upload_storage().open(upload_name(batch), "rb")


def upload_exists(batch: ImportBatch) -> bool:
    return upload_storage().exists(upload_name(batch))


def delete_upload(batch: ImportBatch) -> bool:
    """Delete the batch's file. Missing is fine; an OS error is logged and swallowed (the sweep
    ``purge_stale_uploads`` retries). Returns True when no file is left."""
    storage = upload_storage()
    name = upload_name(batch)
    try:
        if storage.exists(name):
            storage.delete(name)
    except OSError as exc:
        logger.warning("Could not delete the upload of import batch %s: %s", batch.pk, exc)
        return False
    return True


@dataclass(frozen=True, slots=True)
class PurgeReport:
    removed: int
    kept: int


def purge_stale_uploads(
    *, older_than: timedelta = timedelta(days=1), now: datetime | None = None
) -> PurgeReport:
    """Delete files nobody needs any more.

    A file is removed when its batch has ended, when no such batch exists, or when it is older
    than ``older_than`` (a worker that never came back). Files of pending or processing batches
    that are younger than the cut-off stay. Names that are not ``<uuid>.csv`` are never touched.
    """
    storage = upload_storage()
    now = now or timezone.now()
    try:
        _dirs, names = storage.listdir("")
    except FileNotFoundError:
        return PurgeReport(0, 0)
    candidates = {m.group(1): n for n in names if (m := _NAME.match(n))}
    status_of = dict(
        ImportBatch.objects.filter(pk__in=list(candidates)).values_list("pk", "status")
    )
    removed = kept = 0
    for batch_id, name in candidates.items():
        status = status_of.get(uuid.UUID(batch_id))
        age = now - storage.get_modified_time(name)
        stale = status is None or status in BATCH_TERMINAL or age > older_than
        if not stale:
            kept += 1
            continue
        try:
            storage.delete(name)
            removed += 1
        except OSError as exc:
            logger.warning(
                "Could not delete the stale upload of import batch %s: %s", batch_id, exc
            )
            kept += 1
    if removed:
        logger.info("Removed %s stale CSV upload file(s); %s kept.", removed, kept)
    return PurgeReport(removed, kept)
