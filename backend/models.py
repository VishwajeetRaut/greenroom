from typing import Literal, Optional, List
from pydantic import BaseModel, Field


class StartSessionRequest(BaseModel):
    track: Literal["behavioral", "technical", "system-design"]
    role: str = Field(default="Software Engineer", min_length=1, max_length=100)


class StartSessionResponse(BaseModel):
    session_id: str
    track: str
    question: str


class MessageRequest(BaseModel):
    session_id: str
    message: str = Field(min_length=1, max_length=20_000)
    code: Optional[str] = Field(default=None, max_length=100_000)
    language: Optional[str] = Field(default=None, max_length=50)


class MessageResponse(BaseModel):
    question: str
    done: bool = False


class RunCodeRequest(BaseModel):
    language: str = Field(min_length=1, max_length=50)
    version: str = Field(min_length=1, max_length=50)
    source: str = Field(min_length=1, max_length=100_000)
    stdin: Optional[str] = Field(default="", max_length=20_000)


class EndSessionRequest(BaseModel):
    session_id: str


class EvaluationCategory(BaseModel):
    category: str
    score: int
    feedback: str


class STARAnalysis(BaseModel):
    situation: str
    task: str
    action: str
    result: str
    star_score: int
    missing_elements: List[str]


class EndSessionResponse(BaseModel):
    overall_score: int
    summary: str
    star_analysis: Optional[STARAnalysis] = None
    evaluations: List[EvaluationCategory]
