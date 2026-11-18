"""PCM shaping for the Exotel phone leg. Audio is 8 kHz, 16-bit, mono."""

from __future__ import annotations

CHUNK_BYTES = 3200
ALIGN_BYTES = 320


def wav_to_pcm(audio: bytes) -> bytes:
    if not audio.startswith(b"RIFF"):
        return audio
    marker = audio.find(b"data")
    if marker < 0:
        return audio
    size = int.from_bytes(audio[marker + 4 : marker + 8], "little")
    start = marker + 8
    return audio[start : start + size]


class PcmBuffer:
    """Hold incoming audio and release only full phone chunks."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def push(self, pcm: bytes) -> list[bytes]:
        if not pcm:
            return []
        self._buf.extend(pcm)
        ready: list[bytes] = []
        while len(self._buf) >= CHUNK_BYTES:
            ready.append(bytes(self._buf[:CHUNK_BYTES]))
            del self._buf[:CHUNK_BYTES]
        return ready

    def finish(self) -> list[bytes]:
        if not self._buf:
            return []
        tail = bytes(self._buf)
        self._buf.clear()
        return audio_chunks(tail)


def audio_chunks(pcm: bytes) -> list[bytes]:
    if not pcm:
        return []
    remainder = len(pcm) % ALIGN_BYTES
    if remainder:
        pcm += b"\x00" * (ALIGN_BYTES - remainder)
    if len(pcm) < CHUNK_BYTES:
        pcm += b"\x00" * (CHUNK_BYTES - len(pcm))
    pieces = []
    for start in range(0, len(pcm), CHUNK_BYTES):
        piece = pcm[start : start + CHUNK_BYTES]
        if len(piece) < CHUNK_BYTES:
            piece += b"\x00" * (CHUNK_BYTES - len(piece))
        pieces.append(piece)
    return pieces
