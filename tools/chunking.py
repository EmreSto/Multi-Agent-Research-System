import math
import re
import logging
from tools.research_tools import _looks_like_equation
from schemas.chunk_schemas import Chunk

logger = logging.getLogger(__name__)

#Token estimation

def _estimate_tokens(text: str) -> int:
    word_count = len(re.findall(r'\w+', text))
    return math.ceil(word_count * 1.3)


#Equation detection

def _contains_equation(text: str) -> bool:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and len(stripped) < 200 and _looks_like_equation(stripped):
            return True
    return False


#Section splitting

def _split_into_sections(markdown_text: str) -> list[Chunk]:
    sections = []
    current_section = []
    chunk_index = 0
    current_name = "preamble"
    for line in markdown_text.splitlines():
        if re.match(r'^#{1,4}\s+', line.strip()):
            name = re.sub(r'\*+', '', re.sub(r'^#{1,4}\s+', '', line.strip()))
            if not re.search(r'[a-zA-Z0-9]', name):
                current_section.append(line)
                continue
            if "\n".join(current_section).strip():
                sections.append(Chunk(
                    section_name = current_name,
                    content = "\n".join(current_section),
                    has_equations=_contains_equation("\n".join(current_section)),
                    chunk_index=chunk_index,
                    token_count=_estimate_tokens("\n".join(current_section)), 
                ))
                chunk_index += 1
            current_name = name
            current_section = []
        else:
            current_section.append(line)
    if "\n".join(current_section).strip():
        sections.append(Chunk(
            section_name=current_name,
            content="\n".join(current_section),
            has_equations=_contains_equation("\n".join(current_section)),
            chunk_index=chunk_index,
            token_count=_estimate_tokens("\n".join(current_section)),
        ))
    return sections

#Sub-splitting

def _subsplit_large_sections(sections: list[Chunk], max_tokens: int) -> list[Chunk]:
    subsplit_sections = []
    for chunk in sections:
        if chunk.token_count <= max_tokens:
            subsplit_sections.append(chunk)
            continue
        paragraphs = chunk.content.split("\n\n")
        current_group = []
        current_tokens = 0
        groups = []
        for paragraph in paragraphs:
            para_tokens = _estimate_tokens(paragraph)
            if current_tokens + para_tokens > max_tokens and current_group and not _contains_equation(paragraph):
                groups.append("\n\n".join(current_group))
                current_group = []
                current_tokens = 0
            current_group.append(paragraph)
            current_tokens += para_tokens
        if current_group:
            groups.append("\n\n".join(current_group))
        for i, group_content in enumerate(groups):
            subsplit_sections.append(Chunk(
                section_name = f"{chunk.section_name} [{i+1}/{len(groups)}]",
                content=group_content,
                has_equations=_contains_equation(group_content),
                chunk_index=chunk.chunk_index,
                token_count=_estimate_tokens(group_content),
            ))
    for i, sub_chunk in enumerate(subsplit_sections):
        sub_chunk.chunk_index = i

    return subsplit_sections



    