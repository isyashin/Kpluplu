"""Парсер INI-файла базы КонсультантПлюс.

Структура INI:
  [запись 0x8065: весь файл]
    внутри: 21-байтовый заголовок ("F2", номер сборки "362"),
    затем TLV-записи [tag:u16][len:u32][payload] (len включает 6 байт заголовка):
      0x0206 — ?, 0x66 'f' — имя базы ("LAW"), 0x67 'g' — профиль версии,
      5 записей 0x8069 — дескрипторы частей (соответствуют DT1..DT5).

Дескриптор части содержит словарь полей: последовательности TLV-записей
с однобуквенными тегами:
  't' (0x74): имя поля, payload = [field_tag:u8][?:u8][имя CP866 \\0]
  'u' (0x75): дубль имени
  'v' (0x76): внутреннее имя, payload = [0x95 0x04][имя ASCII \\0]
  'w','x','y','z','{','|','}',... — числовые параметры поля
"""
import struct
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TlvRecord:
    tag: int
    payload: bytes
    offset: int


def iter_tlv(buf: bytes, start: int = 0, end: int | None = None):
    """Итератор TLV-записей; останавливается при рассинхронизации."""
    end = end if end is not None else len(buf)
    off = start
    while off + 6 <= end:
        tag, ln = struct.unpack_from("<HI", buf, off)
        if ln < 6 or off + ln > end:
            break
        yield TlvRecord(tag, buf[off + 6: off + ln], off)
        off += ln


@dataclass
class FieldDef:
    tag: int | None = None          # тег поля (первый байт payload записи 't')
    name: str = ""                  # русское имя
    internal: str = ""              # внутреннее имя (COMMON_DOCNAME и т.п.)
    params: dict = field(default_factory=dict)


@dataclass
class PartDef:
    index: int
    fields: list[FieldDef] = field(default_factory=list)
    raw_head: bytes = b""


class IniFile:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        data = self.path.read_bytes()
        outer = next(iter_tlv(data, 0), None)
        if outer is None or outer.tag != 0x8065:
            raise ValueError(f"{path}: не INI КонсультантПлюс")
        body = outer.payload
        # заголовок тела: 03 00 "F2" fd 01 0d000000 9b00 "362"\0 (15 байт)
        self.build = None
        zpos = body.find(b"\x00", 10)
        if 10 <= zpos <= 15:
            self.build = body[10:zpos].decode("ascii", "replace")
        self.base_name = ""
        self.version_profile = ""
        self.parts: list[PartDef] = []
        for rec in iter_tlv(body, 15):
            if rec.tag == 0x66:
                self.base_name = rec.payload[2:].split(b"\x00")[0].decode("ascii", "replace")
            elif rec.tag == 0x67:
                self.version_profile = rec.payload[2:].split(b"\x00")[0].decode("cp866", "replace")
            elif rec.tag == 0x8069:
                self.parts.append(self._parse_part(rec.payload, len(self.parts) + 1))

    def _parse_part(self, payload: bytes, index: int) -> PartDef:
        part = PartDef(index=index, raw_head=payload[:64])
        # дескриптор поля = запись 0x8070: [prefix:u16][вложенные TLV]:
        #   't' (0x74): [field_tag:u8][?:u8][имя CP866 \0]
        #   'v' (0x76): [?:u16][внутреннее имя ASCII \0]
        #   остальные — числовые параметры
        for rec in iter_tlv(payload, 0x0C):
            if rec.tag != 0x8070:
                continue
            f = FieldDef()
            for sub in iter_tlv(rec.payload, 2):
                if sub.tag == 0x74 and len(sub.payload) >= 3:
                    f.tag = sub.payload[0]
                    f.name = sub.payload[2:].split(b"\x00")[0].decode("cp866", "replace")
                elif sub.tag == 0x76 and len(sub.payload) >= 3:
                    f.internal = sub.payload[2:].split(b"\x00")[0].decode("ascii", "replace")
                elif sub.tag not in (0x71, 0x72, 0x75):
                    key = chr(sub.tag) if 32 <= sub.tag < 127 else hex(sub.tag)
                    f.params[key] = sub.payload.hex()
            part.fields.append(f)
        return part

    def field_map(self, part_index: int) -> dict[int, FieldDef]:
        """тег поля -> описание (для части part_index, 1..5)."""
        return {f.tag: f for f in self.parts[part_index - 1].fields if f.tag is not None}


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m consplus.ini PATH_TO_BASE.INI")
    ini = IniFile(sys.argv[1])
    print(f"base={ini.base_name} build={ini.build} profile={ini.version_profile}")
    print(f"parts: {len(ini.parts)}")
    for p in ini.parts:
        print(f"--- part {p.index}: {len(p.fields)} fields")
        if p.index == 1:
            for f in p.fields[:60]:
                print(f"  tag={f.tag:#04x} name={f.name!r} internal={f.internal!r}")
