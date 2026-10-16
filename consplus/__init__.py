"""Чтение баз КонсультантПлюс напрямую из файлов.

Формат: файлы *.DT1..DT5 состоят из TLV-записей [tag:u16][len:u32][payload],
где len включает 6-байтовый заголовок. Сжатые данные — zlib (0x78 0x9c).
- DT1: заголовки документов, записи tag=0x00F1: [crc16:2][zlib-данные]
- DT2: тексты документов, чанки tag=0x0175: [crc16:2][zlib-данные],
       лежат внутри контейнеров 0x00F1
"""
import mmap
import re
import zlib
from pathlib import Path

TAG_CONTAINER = 0x00F1
TAG_TEXT = 0x0175

_ZLIB = b"\x78\x9c"


def try_inflate(buf, max_comp: int) -> bytes | None:
    d = zlib.decompressobj(15)
    try:
        out = d.decompress(buf[: max_comp + 64])
    except zlib.error:
        return None
    if not d.eof or not out:
        return None
    return out


class DtFile:
    """Сканер сжатых записей заданного тега внутри DT-файла."""

    def __init__(self, path: str | Path, tag: int):
        self.path = Path(path)
        self.tag = tag
        self._f = open(self.path, "rb")
        self.mm = mmap.mmap(self._f.fileno(), 0, access=mmap.ACCESS_READ)
        self._pat = re.compile(
            tag.to_bytes(2, "little") + rb"(....)..\x78\x9c", re.DOTALL
        )

    def close(self):
        self.mm.close()
        self._f.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def iter_records(self, limit: int | None = None):
        """Итератор (offset, decompressed_payload)."""
        end = limit if limit else len(self.mm)
        for m in self._pat.finditer(self.mm, 0, end):
            ln = int.from_bytes(m.group(1), "little")
            if not (10 < ln < 4_000_000):
                continue
            comp = ln - 10
            if comp <= 0 or comp > 4_000_000:
                continue
            out = try_inflate(
                self.mm[m.start() + 8: m.start() + 8 + comp + 64], comp
            )
            if out is None or len(out) < 4:
                continue
            yield m.start(), out


class Base:
    """Одна информационная база (каталог BASE/XXX)."""

    def __init__(self, dir_path: str | Path):
        self.dir = Path(dir_path)
        self.name = self.dir.name

    def dt(self, idx: int, tag: int) -> DtFile:
        return DtFile(self.dir / f"{self.name}.DT{idx}", tag)

    def iter_headers(self, limit=None):
        with self.dt(1, TAG_CONTAINER) as f:
            yield from f.iter_records(limit)

    def iter_texts(self, limit=None):
        with self.dt(2, TAG_TEXT) as f:
            yield from f.iter_records(limit)


def parse_header(raw: bytes) -> dict:
    """Заголовок: нуль-терминированные строки CP866 + бинарный хвост.

    Первые поля (по наблюдениям):
      [0] вид документа, [1] номер с префиксом, [2+] название, далее источник...
      Точная раскладка уточняется по словарю INI.
    """
    parts = raw.split(b"\x00")
    fields = []
    for p in parts:
        s = p.decode("cp866", "replace")
        fields.append(s)
    return {"raw_fields": fields}


def parse_text(raw: bytes) -> str:
    """Текст документа: блоки, разделённые нулями, CP866."""
    s = raw.decode("cp866", "replace")
    s = s.strip("\x00")
    s = re.sub(r"\x00+", "\n", s)
    s = re.sub(r"[\x01-\x08\x0b-\x1f]", " ", s)
    s = re.sub(r"[ \t]+", " ", s)
    if "-" * 70 in s:
        s = s.split("-" * 70)[0]
    return s.strip()


def read_record_at(path: str | Path, offset: int) -> bytes | None:
    """Ленивое чтение одной сжатой записи по смещению (напр. текста из DT2)."""
    with open(path, "rb") as f:
        f.seek(offset)
        head = f.read(8)
        if len(head) < 8:
            return None
        tag = int.from_bytes(head[:2], "little")
        ln = int.from_bytes(head[2:6], "little")
        if not (10 < ln < 4_000_000):
            return None
        comp = ln - 10
        f.seek(offset + 8)
        buf = f.read(comp + 64)
        return try_inflate(buf, comp)
