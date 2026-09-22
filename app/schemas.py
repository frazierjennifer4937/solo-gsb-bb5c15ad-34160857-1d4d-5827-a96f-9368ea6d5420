from pydantic import BaseModel, Field, field_validator


# ---------- Participant ----------
class ParticipantIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    group_name: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=255)


class ParticipantOut(ParticipantIn):
    id: int


# ---------- Section ----------
class SectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    capacity: int | None = Field(default=None, ge=1)


class SectionOut(BaseModel):
    id: int
    name: str
    capacity: int | None


# ---------- Table ----------
class TableIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    capacity: int = Field(ge=1, le=1000)
    section_id: int | None = None


class TableOut(BaseModel):
    id: int
    name: str
    capacity: int
    section_id: int | None


# ---------- Rule ----------
_RULE_TYPES = {"together", "separate", "fixed"}


class RuleIn(BaseModel):
    type: str
    participant_ids: list[int] = Field(min_length=1)
    table_id: int | None = None
    seat_no: int | None = Field(default=None, ge=1)
    enabled: bool = True
    note: str | None = Field(default=None, max_length=255)

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        if v not in _RULE_TYPES:
            raise ValueError("type 必须是 together / separate / fixed 之一")
        return v


class RuleUpdate(BaseModel):
    enabled: bool | None = None
    note: str | None = Field(default=None, max_length=255)


class RuleOut(BaseModel):
    id: int
    type: str
    participant_ids: list[int]
    table_id: int | None
    seat_no: int | None
    enabled: bool
    note: str | None


# ---------- Solve / Assignment ----------
class SolveIn(BaseModel):
    # 随机种子：相同输入 + 相同种子 => 相同结果（可重复）
    seed: int = 0
    # 搜索节点上限，避免病态问题长时间运行
    node_limit: int = Field(default=200_000, ge=1, le=5_000_000)


class AssignmentUpdate(BaseModel):
    table_id: int
    seat_no: int = Field(ge=1)
    locked: bool = True


class AssignmentOut(BaseModel):
    id: int
    participant_id: int
    participant_name: str
    group_name: str | None
    table_id: int
    table_name: str
    section_id: int | None
    section_name: str | None
    seat_no: int
    locked: bool
