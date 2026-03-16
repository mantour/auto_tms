"""Pydantic models for progress tracking and plan state."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class MaterialType(str, Enum):
    VIDEO = "video"
    DOCUMENT = "document"
    SURVEY = "survey"
    EXAM = "exam"


class Status(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    SKIPPED = "skipped"


class CourseStatus(str, Enum):
    PENDING = "pending"
    ENROLLED = "enrolled"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class MaterialProgress(BaseModel):
    material_id: str
    material_type: MaterialType
    status: Status = Status.PENDING
    required_minutes: int | None = None
    url: str
    title: str = ""


class CourseProgress(BaseModel):
    course_id: str
    title: str = ""
    status: CourseStatus = CourseStatus.PENDING
    enrolled: bool = False
    materials: list[MaterialProgress] = Field(default_factory=list)


class RunProgress(BaseModel):
    started_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    iteration: int = 1
    courses: dict[str, CourseProgress] = Field(default_factory=dict)


class PlannedCourse(BaseModel):
    course_id: str
    title: str = ""
    url: str = ""
    required: bool = False  # True = 必修, False = 選修
    credit_hours: float = 0.0


class ProgramRequirement(BaseModel):
    program_id: str
    program_name: str = ""
    total_required: float = 0.0
    total_completed: float = 0.0
    mandatory_required: float = 0.0
    mandatory_completed: float = 0.0


class CoursePlan(BaseModel):
    created_at: datetime = Field(default_factory=datetime.now)
    programs: list[ProgramRequirement] = Field(default_factory=list)
    courses: list[PlannedCourse] = Field(default_factory=list)
