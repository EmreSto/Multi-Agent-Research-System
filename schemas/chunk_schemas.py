from pydantic import BaseModel


class Chunk(BaseModel):
    section_name: str
    content: str
    page_number: int | None = None
    has_equations: bool = False
    chunk_index : int = 0
    token_count: int = 0
    paper_title: str = ""
    paper_id: str = ""
