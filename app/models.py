from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Participant(Base):
    __tablename__ = "participants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    group_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)

    assignments: Mapped[list["Assignment"]] = relationship(
        back_populates="participant", cascade="all, delete-orphan"
    )


class Section(Base):
    """分区（例如 A 区 / VIP 区），若干桌位归属一个分区。"""

    __tablename__ = "sections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    # 分区容量上限；None 表示不限（受下属桌位座位总和约束）
    capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)

    tables: Mapped[list["Table"]] = relationship(
        back_populates="section", cascade="all, delete-orphan"
    )


class Table(Base):
    __tablename__ = "tables"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    section_id: Mapped[int | None] = mapped_column(
        ForeignKey("sections.id", ondelete="SET NULL"), nullable=True
    )

    section: Mapped[Section | None] = relationship(back_populates="tables")

    __table_args__ = (CheckConstraint("capacity > 0", name="ck_table_capacity_positive"),)


class Rule(Base):
    """编排规则。

    type:
      - together: participant_ids 中的人必须同桌
      - separate: participant_ids 中的人必须分坐不同桌（两两不同桌）
      - fixed:   participant_ids 中单人（或多人）固定到指定桌/席位
    """

    __tablename__ = "rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    # JSON 数组：参与人 id 列表
    participant_ids: Mapped[str] = mapped_column(String(2000), nullable=False, default="[]")
    table_id: Mapped[int | None] = mapped_column(
        ForeignKey("tables.id", ondelete="CASCADE"), nullable=True
    )
    # 仅 fixed 规则使用：1-based 席位号；None 表示只固定到桌
    seat_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "type IN ('together', 'separate', 'fixed')", name="ck_rule_type"
        ),
    )


class Assignment(Base):
    """求解结果；locked=True 的行由用户锁定，重新求解时保持不变。"""

    __tablename__ = "assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    participant_id: Mapped[int] = mapped_column(
        ForeignKey("participants.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    table_id: Mapped[int] = mapped_column(
        ForeignKey("tables.id", ondelete="CASCADE"), nullable=False
    )
    seat_no: Mapped[int] = mapped_column(Integer, nullable=False)
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    participant: Mapped[Participant] = relationship(back_populates="assignments")

    __table_args__ = (
        UniqueConstraint("table_id", "seat_no", name="uq_table_seat"),
        CheckConstraint("seat_no > 0", name="ck_seat_no_positive"),
    )
