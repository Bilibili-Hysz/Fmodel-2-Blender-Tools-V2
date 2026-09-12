"""Restricted pure-Python reader for UEFormat skeleton identity extensions.

This is intentionally *not* a general UEFormat importer.  It validates the
portable file envelope, optionally decompresses the payload, walks top-level
chunk headers, and decodes only the canonical FModel skeleton identity chunk.
It is suitable for project-index metadata discovery without importing Blender,
meshes, animation tracks, materials, or textures.

Both wire generations are supported: V9 (bare chunk sequence without a header
ObjectPath) and V10 (attribute count header, ObjectPath header field, and
versioned FModel extension envelopes).  The identity payload itself is
unchanged between the two.
"""

from __future__ import annotations

from dataclasses import dataclass
import gzip
import os
from pathlib import Path
import struct
from typing import Any


MAGIC = b"UEFORMAT"
MODEL_IDENTIFIER = "UEMODEL"
ANIM_IDENTIFIER = "UEANIM"
POSE_IDENTIFIER = "UEPOSE"
IDENTITY_EXTENSION_VERSION = 1
# V9 wrote the envelope as [name][count][byte_size][payload]; V10 added the
# logical ObjectPath to the header and wraps FModel extension attributes in a
# versioned envelope: [name][total_size][envelope=1][array_size][payload].
LATEST_UEFORMAT_VERSION = 10
V10_HEADER_OBJECT_PATH_VERSION = 10
V10_ATTRIBUTE_ENVELOPE_VERSION = 1
IDENTITY_SECTIONS = frozenset({"FMODEL_SKELETON_IDENTITY", "FMODEL_TARGET_SKELETON"})

MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_STRING_BYTES = 64 * 1024
MAX_IDENTITY_PATH_BYTES = 16 * 1024
MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_IDENTITY_CHUNK_BYTES = 128 * 1024
MAX_ATTRIBUTE_COUNT = 4096


class UEFormatIdentityError(ValueError):
    """Raised when the UEFormat envelope or identity extension is invalid."""


@dataclass(frozen=True)
class UEFormatIdentityRecord:
    """The small subset of UEFormat metadata needed by the project index."""

    identifier: str
    kind: str
    object_name: str
    file_version: int
    compressed: bool
    identity: dict[str, Any] | None
    identity_extension: str | None

    @property
    def has_strict_identity(self) -> bool:
        return self.identity is not None


def read_ueformat_identity(path: str | os.PathLike[str]) -> UEFormatIdentityRecord:
    """Read one `.uemodel`, `.ueanim`, or `.uepose` identity record.

    A valid file without the relevant identity extension returns a record with
    ``identity=None``.  Callers can classify that as legacy without treating
    it as malformed.  Envelope, compression, chunk, and present-identity
    failures raise ``UEFormatIdentityError``.

    Uncompressed files are decoded through a seekable stream.  Unknown top-level
    chunks (normally the large animation ``TRACKS`` chunk) are skipped instead
    of being copied into Python memory.  Compressed files still need to be
    decompressed as a whole because the UEFormat envelope does not provide
    random-access compressed chunks.
    """

    source = Path(path).expanduser()
    filesystem_source = _filesystem_path(source)
    try:
        size = os.stat(filesystem_source).st_size
    except OSError as error:
        raise UEFormatIdentityError(f"UEFormat file is unavailable: {source}") from error
    if size < len(MAGIC) or size > MAX_FILE_BYTES:
        raise UEFormatIdentityError(f"UEFormat file size is outside the supported range: {size}")
    try:
        with open(filesystem_source, "rb") as stream:
            return _read_ueformat_identity_reader(_FileReader(stream, size))
    except OSError as error:
        raise UEFormatIdentityError(f"UEFormat file cannot be read: {source}") from error


def _filesystem_path(path: Path) -> str:
    """Return a Windows extended path without changing the logical source path."""

    value = os.fspath(path)
    if os.name != "nt" or not os.path.isabs(value) or value.startswith("\\\\?\\"):
        return value
    return "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value


def read_ueformat_identity_bytes(data: bytes) -> UEFormatIdentityRecord:
    """Decode a synthetic or on-disk UEFormat byte sequence held in memory."""

    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("UEFormat data must be bytes-like")
    raw = bytes(data)
    if len(raw) < len(MAGIC) or len(raw) > MAX_FILE_BYTES:
        raise UEFormatIdentityError("UEFormat data size is outside the supported range")

    return _read_ueformat_identity_reader(_Reader(raw))


def _read_ueformat_identity_reader(reader) -> UEFormatIdentityRecord:
    if reader.read(len(MAGIC)) != MAGIC:
        raise UEFormatIdentityError("Invalid UEFormat magic")
    identifier = reader.read_fstring("identifier", MAX_STRING_BYTES)
    kind = {
        MODEL_IDENTIFIER: "model",
        ANIM_IDENTIFIER: "animation",
        POSE_IDENTIFIER: "pose",
    }.get(identifier)
    if kind is None:
        raise UEFormatIdentityError(f"Unsupported UEFormat identifier: {identifier}")

    file_version = reader.read_u8("file version")
    if file_version > LATEST_UEFORMAT_VERSION:
        raise UEFormatIdentityError(f"Unsupported UEFormat file version: {file_version}")
    object_name = reader.read_fstring("object name", MAX_STRING_BYTES)
    if file_version >= V10_HEADER_OBJECT_PATH_VERSION:
        # V10 inserted the logical ObjectPath between the object name and the
        # compression flag; the identity contract itself is unchanged.
        reader.read_fstring("object path", MAX_STRING_BYTES)
    compressed = reader.read_bool()
    archive = reader
    if compressed:
        archive = _decompress_archive(reader)

    v10_attributes = file_version >= V10_HEADER_OBJECT_PATH_VERSION
    identity_section = (
        "FMODEL_SKELETON_IDENTITY" if kind == "model" else "FMODEL_TARGET_SKELETON"
    )
    identity = None
    seen_identity = False
    seen_sections: set[str] = set()
    # V10 serializes the root as an attribute set with an explicit count; V9
    # wrote a bare chunk sequence that ends at EOF.
    if v10_attributes:
        attribute_count = archive.read_i32("attribute count")
        if attribute_count < 0 or attribute_count > MAX_ATTRIBUTE_COUNT:
            raise UEFormatIdentityError(f"Invalid attribute count: {attribute_count}")
    else:
        attribute_count = None
    processed_attributes = 0
    while not archive.eof():
        if attribute_count is not None:
            if processed_attributes >= attribute_count:
                raise UEFormatIdentityError("UEFormat archive has trailing data")
            processed_attributes += 1
        section_name = archive.read_fstring("top-level section", MAX_STRING_BYTES)
        if section_name in seen_sections and section_name in IDENTITY_SECTIONS:
            raise UEFormatIdentityError(f"Duplicate identity section: {section_name}")
        seen_sections.add(section_name)

        if section_name != identity_section:
            # Skip every non-target attribute, including the other FMODEL_*
            # sections. V10 standard attributes (LODS, TRACKS, ...) carry a
            # plain payload; the FModel envelope only wraps FMODEL_* data.
            if v10_attributes:
                total_size = archive.read_i32("top-level attribute size")
                if total_size < 0 or total_size > archive.remaining():
                    raise UEFormatIdentityError(
                        f"Invalid attribute size for section {section_name}: {total_size}"
                    )
                archive.skip(total_size, f"section {section_name} payload")
            else:
                array_size = archive.read_i32("top-level array_size")
                byte_size = archive.read_i32("top-level byte_size")
                if array_size < 0:
                    raise UEFormatIdentityError(
                        f"Invalid negative array_size for section {section_name}: {array_size}"
                    )
                if byte_size < 0 or byte_size > archive.remaining():
                    raise UEFormatIdentityError(
                        f"Invalid byte_size for section {section_name}: {byte_size}"
                    )
                archive.skip(byte_size, f"section {section_name} payload")
            continue

        if v10_attributes:
            total_size = archive.read_i32("identity attribute size")
            if total_size < 5 or total_size > archive.remaining():
                raise UEFormatIdentityError(
                    f"Invalid identity attribute size: {total_size}"
                )
            envelope_version = archive.read_u8("identity envelope version")
            if envelope_version != V10_ATTRIBUTE_ENVELOPE_VERSION:
                raise UEFormatIdentityError(
                    f"Unsupported identity attribute envelope version: {envelope_version}"
                )
            array_size = archive.read_i32("identity array_size")
            byte_size = total_size - 5
        else:
            array_size = archive.read_i32("top-level array_size")
            byte_size = archive.read_i32("top-level byte_size")
            if array_size < 0:
                raise UEFormatIdentityError(
                    f"Invalid negative array_size for section {section_name}: {array_size}"
                )
            if byte_size < 0 or byte_size > archive.remaining():
                raise UEFormatIdentityError(
                    f"Invalid byte_size for section {section_name}: {byte_size}"
                )

        if seen_identity:
            raise UEFormatIdentityError(f"Duplicate expected identity section: {section_name}")
        if array_size != 1:
            raise UEFormatIdentityError(
                f"Invalid identity section array_size: {array_size}"
            )
        if byte_size > MAX_IDENTITY_CHUNK_BYTES:
            raise UEFormatIdentityError("Identity chunk exceeds the supported safety limit")
        identity = _read_identity_chunk(archive.read(byte_size), kind)
        seen_identity = True

    if not archive.eof():
        raise UEFormatIdentityError("UEFormat archive has trailing data")
    if attribute_count is not None and processed_attributes != attribute_count:
        raise UEFormatIdentityError("UEFormat archive is missing declared attributes")
    if reader.remaining() != 0:
        raise UEFormatIdentityError("UEFormat envelope has trailing data")

    return UEFormatIdentityRecord(
        identifier=identifier,
        kind=kind,
        object_name=object_name,
        file_version=file_version,
        compressed=compressed,
        identity=identity,
        identity_extension=identity_section if seen_identity else None,
    )


def read_ueformat_provenance(path: str | os.PathLike[str]):
    """Return a `SkeletonProvenance` classification for one UEFormat file."""

    module = _provenance_module()
    record = read_ueformat_identity(path)
    return module.inspect_skeleton_provenance(record.identity)


def _read_identity_chunk(chunk: bytes, kind: str) -> dict[str, Any]:
    reader = _Reader(chunk)
    extension_version = reader.read_u8("identity extension version")
    if extension_version != IDENTITY_EXTENSION_VERSION:
        raise UEFormatIdentityError(
            f"Unsupported FModel identity extension version: {extension_version}"
        )

    identity: dict[str, Any]
    if kind == "model":
        identity = _read_base_identity(reader)
    elif kind == "animation":
        source_path = reader.read_identity_fstring("source_animation_path")
        identity = _read_base_identity(reader)
        identity["source_animation_path"] = source_path
    elif kind == "pose":
        source_path = reader.read_identity_fstring("source_pose_path")
        identity = _read_base_identity(reader)
        identity["source_pose_path"] = source_path
    else:  # pragma: no cover - kind is constrained by the envelope.
        raise UEFormatIdentityError(f"Unsupported identity kind: {kind}")

    if not reader.eof():
        raise UEFormatIdentityError("Identity chunk has trailing data")
    try:
        return _provenance_module().normalize_skeleton_identity(identity)
    except (TypeError, ValueError) as error:
        raise UEFormatIdentityError(str(error)) from error


def _read_base_identity(reader: "_Reader") -> dict[str, Any]:
    skeleton_path = reader.read_identity_fstring("skeleton_path")
    has_guid = reader.read_u8("has_skeleton_guid")
    if has_guid not in (0, 1):
        raise UEFormatIdentityError(f"Invalid has_skeleton_guid flag: {has_guid}")
    guid = reader.read(16) if has_guid else None
    layout_hash = reader.read(32, "bone_layout_hash")
    return {
        "version": IDENTITY_EXTENSION_VERSION,
        "skeleton_path": skeleton_path,
        "has_skeleton_guid": bool(has_guid),
        "skeleton_guid": guid,
        "bone_layout_hash": layout_hash,
    }


def _decompress_archive(reader: "_Reader") -> "_Reader":
    compression_type = reader.read_fstring("compression type", MAX_STRING_BYTES)
    uncompressed_size = reader.read_i32("uncompressed size")
    compressed_size = reader.read_i32("compressed size")
    if uncompressed_size < 0 or uncompressed_size > MAX_UNCOMPRESSED_BYTES:
        raise UEFormatIdentityError(
            f"Invalid uncompressed size: {uncompressed_size}"
        )
    if compressed_size < 0 or compressed_size != reader.remaining():
        raise UEFormatIdentityError(
            f"Compressed payload size does not match header: {compressed_size}"
        )
    compressed = reader.read(compressed_size, "compressed payload")
    try:
        if compression_type == "GZIP":
            decompressed = gzip.decompress(compressed)
        elif compression_type == "ZSTD":
            decompressed = _zstd_decompress(compressed, uncompressed_size)
        else:
            raise UEFormatIdentityError(f"Unknown compression type: {compression_type}")
    except UEFormatIdentityError:
        raise
    except Exception as error:
        raise UEFormatIdentityError(
            f"Unable to decompress UEFormat {compression_type} payload"
        ) from error
    if len(decompressed) != uncompressed_size:
        raise UEFormatIdentityError(
            "Decompressed payload size does not match header"
        )
    return _Reader(decompressed)


def _zstd_decompress(data: bytes, expected_size: int) -> bytes:
    try:
        from compression import zstd
    except ImportError:
        try:
            import zstandard as zstd  # type: ignore[import-not-found]
        except ImportError as error:
            raise UEFormatIdentityError(
                "ZSTD UEFormat identity requires Python compression.zstd or zstandard"
            ) from error
        return zstd.ZstdDecompressor().decompress(data, max_output_size=expected_size)
    # Python 3.14's stdlib API does not expose max_output_size; the outer
    # file and declared uncompressed-size ceilings still bound this probe,
    # and the exact size is verified by the caller.
    return zstd.decompress(data)


def _provenance_module():
    try:
        from . import skeleton_provenance
    except ImportError:  # Allows direct pure-Python module loading in probes.
        import skeleton_provenance
    return skeleton_provenance


class _FileReader:
    """Bounded reader over an on-disk UEFormat file.

    ``skip`` uses the file cursor for non-identity chunks, so probing a large
    uncompressed animation does not allocate or copy its track payload.
    """

    def __init__(self, stream, size: int):
        self.stream = stream
        self._remaining = size

    def eof(self) -> bool:
        return self._remaining == 0

    def remaining(self) -> int:
        return self._remaining

    def read(self, size: int, description: str = "data") -> bytes:
        if size < 0 or size > self._remaining:
            raise UEFormatIdentityError(f"{description} exceeds available data")
        value = self.stream.read(size)
        if len(value) != size:
            raise UEFormatIdentityError(f"{description} exceeds available data")
        self._remaining -= size
        return value

    def skip(self, size: int, description: str = "data") -> None:
        if size < 0 or size > self._remaining:
            raise UEFormatIdentityError(f"{description} exceeds available data")
        try:
            self.stream.seek(size, os.SEEK_CUR)
        except (OSError, ValueError) as error:
            raise UEFormatIdentityError(f"Unable to skip {description}") from error
        self._remaining -= size

    def read_u8(self, description: str) -> int:
        return self.read(1, description)[0]

    def read_bool(self) -> bool:
        value = self.read_u8("compressed flag")
        if value not in (0, 1):
            raise UEFormatIdentityError(f"Invalid compressed flag: {value}")
        return bool(value)

    def read_i32(self, description: str) -> int:
        return struct.unpack("<i", self.read(4, description))[0]

    def read_fstring(self, description: str, maximum: int) -> str:
        size = self.read_i32(f"{description} length")
        if size <= 0 or size > maximum:
            raise UEFormatIdentityError(f"Invalid {description} length: {size}")
        raw = self.read(size, description)
        if raw.endswith(b"\0"):
            raw = raw[:-1]
        if not raw or b"\0" in raw:
            raise UEFormatIdentityError(f"Invalid {description}")
        try:
            return raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise UEFormatIdentityError(f"Invalid UTF-8 in {description}") from error

    def read_identity_fstring(self, description: str) -> str:
        value = self.read_fstring(description, MAX_IDENTITY_PATH_BYTES)
        if value.isspace() or value != value.strip():
            raise UEFormatIdentityError(f"Invalid {description}")
        return value


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.position = 0

    def eof(self) -> bool:
        return self.position == len(self.data)

    def remaining(self) -> int:
        return len(self.data) - self.position

    def read(self, size: int, description: str = "data") -> bytes:
        if size < 0 or size > self.remaining():
            raise UEFormatIdentityError(f"{description} exceeds available data")
        start = self.position
        self.position += size
        return self.data[start:self.position]

    def skip(self, size: int, description: str = "data") -> None:
        if size < 0 or size > self.remaining():
            raise UEFormatIdentityError(f"{description} exceeds available data")
        self.position += size

    def read_u8(self, description: str) -> int:
        return self.read(1, description)[0]

    def read_bool(self) -> bool:
        value = self.read_u8("compressed flag")
        if value not in (0, 1):
            raise UEFormatIdentityError(f"Invalid compressed flag: {value}")
        return bool(value)

    def read_i32(self, description: str) -> int:
        return struct.unpack("<i", self.read(4, description))[0]

    def read_fstring(self, description: str, maximum: int) -> str:
        size = self.read_i32(f"{description} length")
        if size <= 0 or size > maximum:
            raise UEFormatIdentityError(f"Invalid {description} length: {size}")
        raw = self.read(size, description)
        if raw.endswith(b"\0"):
            raw = raw[:-1]
        if not raw or b"\0" in raw:
            raise UEFormatIdentityError(f"Invalid {description}")
        try:
            return raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise UEFormatIdentityError(f"Invalid UTF-8 in {description}") from error

    def read_identity_fstring(self, description: str) -> str:
        value = self.read_fstring(description, MAX_IDENTITY_PATH_BYTES)
        if value.isspace() or value != value.strip():
            raise UEFormatIdentityError(f"Invalid {description}")
        return value


__all__ = [
    "ANIM_IDENTIFIER",
    "IDENTITY_EXTENSION_VERSION",
    "LATEST_UEFORMAT_VERSION",
    "MAGIC",
    "MODEL_IDENTIFIER",
    "POSE_IDENTIFIER",
    "UEFormatIdentityError",
    "UEFormatIdentityRecord",
    "read_ueformat_identity",
    "read_ueformat_identity_bytes",
    "read_ueformat_provenance",
]
