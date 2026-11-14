"""Content chunking strategies for RAG consumption."""
import re


def chunk_recursive(text: str, max_tokens: int = 512, overlap: int = 50) -> list[dict]:
    """Split text into chunks of roughly max_tokens words with overlap."""
    words = text.split()
    if len(words) <= max_tokens:
        return [{"index": 0, "text": text, "word_count": len(words)}]

    chunks = []
    start = 0
    index = 0
    while start < len(words):
        end = min(start + max_tokens, len(words))
        chunk_words = words[start:end]
        chunks.append({
            "index": index,
            "text": " ".join(chunk_words),
            "word_count": len(chunk_words),
            "start_word": start,
            "end_word": end,
        })
        index += 1
        start = end - overlap
        if start >= len(words):
            break
    return chunks


def chunk_by_headings(markdown: str) -> list[dict]:
    """Split markdown at heading boundaries, preserving heading context."""
    lines = markdown.split("\n")
    chunks = []
    current_chunk = []
    current_heading = ""
    index = 0

    for line in lines:
        if re.match(r"^#{1,3}\s", line):
            # Flush previous chunk
            if current_chunk:
                text = "\n".join(current_chunk).strip()
                if text:
                    chunks.append({
                        "index": index,
                        "text": text,
                        "heading": current_heading,
                        "word_count": len(text.split()),
                    })
                    index += 1
            current_heading = line.strip()
            current_chunk = [line]
        else:
            current_chunk.append(line)

    # Flush last chunk
    if current_chunk:
        text = "\n".join(current_chunk).strip()
        if text:
            chunks.append({
                "index": index,
                "text": text,
                "heading": current_heading,
                "word_count": len(text.split()),
            })

    return chunks
