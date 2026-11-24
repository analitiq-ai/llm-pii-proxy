"""Line-based chunker shared by local NER and the confidential pass."""

from dataclasses import dataclass

# A value cut by a hard split is seen whole in one of the two overlapping pieces.
DEFAULT_OVERLAP = 200


@dataclass(frozen=True, slots=True)
class Chunk:
    start: int
    text: str


def chunk(text: str, max_chars: int, overlap: int = DEFAULT_OVERLAP) -> list[Chunk]:
    """Pack whole lines into chunks of at most max_chars; split a longer line hard, with overlap."""
    if not 0 <= overlap < max_chars:
        raise ValueError("overlap must be smaller than max_chars")
    chunks: list[Chunk] = []
    current_start, current_len = 0, 0
    pos = 0
    for line in text.splitlines(keepends=True):
        if current_len + len(line) > max_chars and current_len:
            chunks.append(Chunk(current_start, text[current_start : current_start + current_len]))
            current_len = 0
        if len(line) > max_chars:
            chunks.extend(_hard_split(text, pos, len(line), max_chars, overlap))
        else:
            if not current_len:
                current_start = pos
            current_len += len(line)
        pos += len(line)
    if current_len:
        chunks.append(Chunk(current_start, text[current_start : current_start + current_len]))
    return chunks


def _hard_split(text: str, start: int, length: int, max_chars: int, overlap: int) -> list[Chunk]:
    end = start + length
    pieces = []
    piece_start = start
    while True:
        pieces.append(Chunk(piece_start, text[piece_start : min(piece_start + max_chars, end)]))
        if piece_start + max_chars >= end:
            return pieces
        piece_start += max_chars - overlap
