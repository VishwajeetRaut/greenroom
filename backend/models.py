import uuid
from typing import Annotated, List, Literal, Optional

from pydantic import AfterValidator, BaseModel, Field


def _validate_session_id(v: str) -> str:
    try:
        uuid.UUID(v)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("session_id must be a valid UUID") from exc
    return v


# Session ids are always generated server-side via uuid.uuid4() (see
# routers/interview.py::start_session), so any request carrying one that
# isn't a well-formed UUID is either a bug in the caller or a probe — reject
# it at the request-validation boundary instead of letting it reach Supabase.
SessionId = Annotated[str, AfterValidator(_validate_session_id)]


class StartSessionRequest(BaseModel):
    track: Literal["behavioral", "technical", "system-design"]
    role: str = Field(default="Software Engineer", min_length=1, max_length=100)
    user_id: Optional[str] = None
    job_description: Optional[str] = Field(default=None, max_length=5000)


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
    session_id: SessionId
    language: str = Field(min_length=1, max_length=50)
    version: str = Field(min_length=1, max_length=50)
    source: str = Field(min_length=1, max_length=100_000)


class RunTestsResponse(BaseModel):
    status: str
    visible_tests: List[VisibleTestResult]
    hidden_tests: List[HiddenTestResult]
    passed: int
    total: int
    compile_error: Optional[str] = None
    error_type: Optional[Literal["transient", "permanent"]] = None


class StartSessionResponse(BaseModel):
    session_id: str
    track: str
    question: str


class MessageRequest(BaseModel):
    session_id: SessionId
    message: str = Field(min_length=1, max_length=20_000)
    code: Optional[str] = Field(default=None, max_length=100_000)
    language: Optional[str] = Field(default=None, max_length=50)


class QuestionContext(BaseModel):
    """Sent to the frontend once per session, when the coding problem is first assigned."""
    id: str
    title: str
    difficulty: str
    prompt: str
    constraints: List[str]
    examples: List[dict]
    is_stdio: bool


class MessageResponse(BaseModel):
    question: str
    done: bool = False
    question_context: Optional[QuestionContext] = None


class BoilerplateResponse(BaseModel):
    boilerplate: Optional[str] = None
    supported: bool = True


class HistoryMessage(BaseModel):
    role: str
    content: str


class ResumeSessionResponse(BaseModel):
    session_id: str
    track: str
    history: List[HistoryMessage]
    question_context: Optional[QuestionContext] = None
    diagram_elements: List[dict] = []


class SaveDiagramRequest(BaseModel):
    session_id: SessionId
    elements: List[dict] = Field(default_factory=list, max_length=5000)


class EndSessionRequest(BaseModel):
    session_id: SessionId


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


class DiagramEvaluation(BaseModel):
    components_found: List[str]
    components_missing: List[str]
    proximity_score: int = Field(ge=0, le=10)
    proximity_label: Literal["needs work", "reasonable", "strong"]
    feedback: str


class EndSessionResponse(BaseModel):
    overall_score: int
    summary: str
    star_analysis: Optional[STARAnalysis] = None
    evaluations: List[EvaluationCategory]
    diagram_evaluation: Optional[DiagramEvaluation] = None


class AnalyticsEventRequest(BaseModel):
    event: str = Field(min_length=1, max_length=100)
    session_id: Optional[SessionId] = None
    properties: Optional[dict] = None
