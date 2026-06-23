from typing import Optional, List
from pydantic import BaseModel


class StartSessionRequest(BaseModel):
    track: str
    role: Optional[str] = "Software Engineer"
    user_id: Optional[str] = None


class VisibleTestResult(BaseModel):
    id: int
    label: str
    input: str
    expected: str
    output: Optional[str] = None
    error: Optional[str] = None
    passed: bool


class HiddenTestResult(BaseModel):
    id: int
    passed: bool


class RunTestsRequest(BaseModel):
    session_id: str
    language: str
    version: str
    source: str


class RunTestsResponse(BaseModel):
    status: str
    visible_tests: List[VisibleTestResult]
    hidden_tests: List[HiddenTestResult]
    passed: int
    total: int
    compile_error: Optional[str] = None


class StartSessionResponse(BaseModel):
    session_id: str
    track: str
    question: str


class MessageRequest(BaseModel):
    session_id: str
    message: str
    code: Optional[str] = None
    language: Optional[str] = None


class MessageResponse(BaseModel):
    question: str
    done: bool = False


class RunCodeRequest(BaseModel):
    language: str
    version: str
    source: str
    stdin: Optional[str] = ""


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
