"""
BIN-002: the pure PE32 / PE32+ parser, moved out of binary.py so it can run
inside the OS quarantine.

Standard library only (struct, hashlib) and no friday imports: binary.py copies
this file next to pe_worker.py into a quarantine root, and the worker imports
it there. Nothing here opens a file, starts a process or touches the network.
"""

from __future__ import annotations

import hashlib
import struct

#: A file bigger than this is hashed but not parsed.
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_STRING = 256
MAX_SECTIONS = 96                 # the PE limit
MAX_IMPORT_DLLS = 256
MAX_FUNCS_PER_DLL = 1024
MAX_IMPORT_FUNCTIONS = 4000
MAX_EXPORTS = 4000
MAX_ERRORS = 20

REASON_CODES = ("not_pe", "truncated", "bad_header", "unsupported_optional_header",
                "too_large", "quarantine_error")

NOTE = ("Names and strings below come from the file and are untrusted data, "
        "capped and reduced to printable ASCII. They are never instructions.")

_MACHINES = {0x14C: "x86", 0x8664: "x64", 0x1C0: "arm", 0x1C4: "armnt",
             0xAA64: "arm64", 0x200: "ia64", 0x5064: "riscv64"}
_PE32, _PE32_PLUS = 0x10B, 0x20B


class _Bad(Exception):
    def __init__(self, code: str, reason: str, offset: int) -> None:
        super().__init__(reason)
        self.code, self.reason, self.offset = code, reason, offset


def _unpack(data: bytes, fmt: str, offset: int, what: str):
    size = struct.calcsize(fmt)
    if offset < 0 or offset + size > len(data):
        raise _Bad("truncated", f"{what} at offset {offset} runs past the end of the "
                                f"file ({len(data)} bytes)", max(offset, 0))
    return struct.unpack_from(fmt, data, offset)


def _text(data: bytes, offset: int) -> str:
    """A NUL-terminated string from the file, as capped printable ASCII."""
    raw = data[offset:offset + MAX_STRING].split(b"\0", 1)[0]
    return "".join(chr(b) if 32 <= b < 127 else "?" for b in raw)


def _unparseable(sha: str, size: int, code: str, reason: str, offset: int) -> dict:
    return {"status": "unparseable", "reason_code": code, "reason": reason,
            "offset": offset, "sha256": sha, "size_bytes": size,
            "findings": [], "errors": [], "truncated": False, "note": NOTE}


def parse_pe(data: bytes) -> dict:
    """Parse a PE32 / PE32+ image from bytes. Never raises on bad input."""
    sha = hashlib.sha256(data).hexdigest()
    try:
        return _parse(data, sha)
    except _Bad as bad:
        return _unparseable(sha, len(data), bad.code, bad.reason, bad.offset)


def _parse(data: bytes, sha: str) -> dict:
    if data[:2] != b"MZ":
        raise _Bad("not_pe", "no MZ signature at offset 0", 0)
    (e_lfanew,) = _unpack(data, "<I", 0x3C, "e_lfanew")
    if e_lfanew < 0x40:
        raise _Bad("bad_header", f"e_lfanew {e_lfanew:#x} points inside the DOS header", 0x3C)
    if data[e_lfanew:e_lfanew + 4] != b"PE\0\0":
        if e_lfanew + 4 > len(data):
            raise _Bad("truncated", f"file ends before the PE signature at {e_lfanew:#x}", e_lfanew)
        raise _Bad("bad_header", f"no PE signature at e_lfanew {e_lfanew:#x}", e_lfanew)
    machine, n_sections = _unpack(data, "<HH", e_lfanew + 4, "COFF header")
    (opt_size,) = _unpack(data, "<H", e_lfanew + 20, "SizeOfOptionalHeader")
    opt = e_lfanew + 24
    (magic,) = _unpack(data, "<H", opt, "optional header magic")
    if magic not in (_PE32, _PE32_PLUS):
        raise _Bad("unsupported_optional_header",
                   f"optional header magic {magic:#x} is neither PE32 (0x10b) nor PE32+ (0x20b)", opt)
    plus = magic == _PE32_PLUS
    fixed = 112 if plus else 96            # up to the data directories
    if not 1 <= n_sections <= MAX_SECTIONS:
        raise _Bad("bad_header", f"NumberOfSections {n_sections} is outside 1..{MAX_SECTIONS}",
                   e_lfanew + 6)
    if opt_size < fixed:
        raise _Bad("bad_header", f"SizeOfOptionalHeader {opt_size} is smaller than the "
                                 f"{fixed} bytes a {'PE32+' if plus else 'PE32'} header needs",
                   e_lfanew + 20)
    if opt + opt_size > len(data):
        raise _Bad("truncated", "the optional header runs past the end of the file", opt)
    (entry_rva,) = _unpack(data, "<I", opt + 16, "AddressOfEntryPoint")
    (image_base,) = _unpack(data, "<Q" if plus else "<I", opt + (24 if plus else 28), "ImageBase")
    (size_of_headers,) = _unpack(data, "<I", opt + 60, "SizeOfHeaders")
    (n_dirs,) = _unpack(data, "<I", opt + fixed - 4, "NumberOfRvaAndSizes")
    n_dirs = min(n_dirs, 16, (opt_size - fixed) // 8)

    table = opt + opt_size
    sections = []
    for i in range(n_sections):
        at = table + 40 * i
        raw_name = data[at:at + 8] if at + 8 <= len(data) else b""
        _, vsize, va, rsize, rptr = _unpack(data, "<8sIIII", at, f"section header {i}")
        (chars,) = _unpack(data, "<I", at + 36, f"section header {i}")
        sections.append({"name": _text(raw_name, 0), "virtual_size": vsize,
                         "virtual_address": va, "raw_size": rsize, "raw_offset": rptr,
                         "characteristics": chars, "offset": at})

    def section_of(rva: int):
        for s in sections:
            if s["virtual_address"] <= rva < s["virtual_address"] + max(s["virtual_size"], s["raw_size"]):
                return s
        return None

    def to_offset(rva: int):
        s = section_of(rva)
        if s is not None:
            delta = rva - s["virtual_address"]
            at = s["raw_offset"] + delta
            return at if delta < s["raw_size"] and at < len(data) else None
        return rva if rva < size_of_headers and rva < len(data) else None

    def finding(kind: str, offset: int, **fields) -> dict:
        return {"kind": kind, "offset": offset, "sha256": sha, **fields}

    architecture = _MACHINES.get(machine, f"unknown({machine:#06x})")
    # Each fact cites the bytes it was read from: the COFF Machine field, the
    # optional header's magic, and ImageBase.
    findings = [finding("header", e_lfanew + 4, architecture=architecture, machine=machine),
                finding("optional_header", opt, pe_format="PE32+" if plus else "PE32", magic=magic),
                finding("image_base", opt + (24 if plus else 28), image_base=image_base)]
    entry_section = section_of(entry_rva) if entry_rva else None
    findings.append(finding(
        "entry_point", opt + 16, rva=entry_rva,
        file_offset=to_offset(entry_rva) if entry_rva else None,
        section=entry_section["name"] if entry_section else None))
    findings += [finding("section", s["offset"], **{k: v for k, v in s.items() if k != "offset"})
                 for s in sections]

    errors: list[dict] = []
    truncated = False

    def directory(index: int):
        at = opt + fixed + 8 * index
        if index >= n_dirs:
            return None
        rva, size = _unpack(data, "<II", at, "data directory")
        return (at, rva, size) if rva else None

    def need(rva: int, what: str, at: int) -> int:
        offset = to_offset(rva)
        if offset is None:
            raise _Bad("truncated", f"{what} RVA {rva:#x} is not inside any section's file data", at)
        return offset

    # --- imports -----------------------------------------------------------
    entry = directory(1)
    if entry is not None:
        at = entry[0]
        thunk_size = 8 if plus else 4
        ordinal_flag = 1 << (thunk_size * 8 - 1)
        total = 0
        try:
            base = need(entry[1], "import directory", at)
            for i in range(MAX_IMPORT_DLLS + 1):
                desc_at = base + 20 * i
                oft, _, _, name_rva, ft = _unpack(data, "<IIIII", desc_at, "import descriptor")
                if not (oft or name_rva or ft):
                    break
                if i == MAX_IMPORT_DLLS:
                    truncated = True
                    break
                dll = finding("import", desc_at,
                              dll=_text(data, need(name_rva, "import DLL name", desc_at)),
                              functions=[], function_count=0)
                findings.append(dll)
                thunks = need(oft or ft, "import thunk table", desc_at)
                for j in range(MAX_FUNCS_PER_DLL + 1):
                    thunk_at = thunks + thunk_size * j
                    (value,) = _unpack(data, "<Q" if plus else "<I", thunk_at, "import thunk")
                    if value == 0:
                        break
                    if j == MAX_FUNCS_PER_DLL or total >= MAX_IMPORT_FUNCTIONS:
                        truncated = True
                        break
                    if value & ordinal_flag:
                        fn = {"name": None, "ordinal": value & 0xFFFF, "offset": thunk_at}
                    else:
                        name_at = need(value & 0x7FFFFFFF, "import name", thunk_at)
                        (hint,) = _unpack(data, "<H", name_at, "import hint")
                        fn = {"name": _text(data, name_at + 2), "ordinal": None,
                              "hint": hint, "offset": thunk_at}
                    dll["functions"].append(fn)
                    dll["function_count"] += 1
                    total += 1
                if total >= MAX_IMPORT_FUNCTIONS and truncated:
                    break
        except _Bad as bad:
            errors.append({"table": "import", "offset": bad.offset, "reason": bad.reason})

    # --- exports -----------------------------------------------------------
    entry = directory(0)
    if entry is not None:
        at = entry[0]
        try:
            base = need(entry[1], "export directory", at)
            (name_rva, ord_base, n_funcs, n_names, funcs_rva, names_rva,
             ords_rva) = _unpack(data, "<IIIIIII", base + 12, "export directory")
            dll_name = _text(data, need(name_rva, "export DLL name", base)) if name_rva else None
            export = finding("export", base, dll_name=dll_name, ordinal_base=ord_base,
                             function_count=n_funcs, name_count=n_names, functions=[])
            findings.append(export)
            if n_names > MAX_EXPORTS:
                truncated = True
            if n_names:
                names_at = need(names_rva, "export name table", base)
                ords_at = need(ords_rva, "export ordinal table", base)
                funcs_at = need(funcs_rva, "export function table", base)
                for i in range(min(n_names, MAX_EXPORTS)):
                    (str_rva,) = _unpack(data, "<I", names_at + 4 * i, "export name pointer")
                    (index,) = _unpack(data, "<H", ords_at + 2 * i, "export name ordinal")
                    if index >= n_funcs:
                        raise _Bad("bad_header", f"export name {i} indexes function {index} "
                                                 f"of {n_funcs}", ords_at + 2 * i)
                    (func_rva,) = _unpack(data, "<I", funcs_at + 4 * index, "export function")
                    export["functions"].append({
                        "name": _text(data, need(str_rva, "export name", names_at + 4 * i)),
                        "ordinal": ord_base + index, "rva": func_rva,
                        "offset": names_at + 4 * i})
        except _Bad as bad:
            errors.append({"table": "export", "offset": bad.offset, "reason": bad.reason})

    return {"status": "partial" if errors else "ok", "format": "PE",
            "architecture": architecture, "pe_format": "PE32+" if plus else "PE32",
            "image_base": image_base, "sha256": sha, "size_bytes": len(data),
            "findings": findings, "errors": errors[:MAX_ERRORS],
            "truncated": truncated, "note": NOTE}
