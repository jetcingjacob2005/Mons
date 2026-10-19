"""Persistence for the Discord automation; Postgres in production, SQLite locally."""
from __future__ import annotations

import os, json
from datetime import datetime, timezone
from typing import Any
from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

def utcnow() -> datetime: return datetime.now(timezone.utc)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///mons_bot.db")
if DATABASE_URL.startswith("postgres://"): DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"): DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})

class Base(DeclarativeBase): pass
class Submission(Base):
    __tablename__ = "submissions"; __table_args__ = (UniqueConstraint("attachment_id", name="uq_submission_attachment"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True); attachment_id: Mapped[str] = mapped_column(String(32), nullable=False)
    message_id: Mapped[str] = mapped_column(String(32), nullable=False); guild_id: Mapped[str] = mapped_column(String(32), nullable=False); channel_id: Mapped[str] = mapped_column(String(32), nullable=False)
    user_id: Mapped[str] = mapped_column(String(32), nullable=False); user_display: Mapped[str] = mapped_column(String(200), nullable=False); filename: Mapped[str] = mapped_column(String(300), nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False); review_status: Mapped[str] = mapped_column(String(20), nullable=False, default="not_required")
    result_json: Mapped[str] = mapped_column(Text, nullable=False); reviewer_id: Mapped[str | None] = mapped_column(String(32)); review_message_id: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow); reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
class KarmaLedger(Base):
    __tablename__ = "karma_ledger"
    id: Mapped[int] = mapped_column(Integer, primary_key=True); user_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True); user_display: Mapped[str] = mapped_column(String(200), nullable=False)
    points: Mapped[int] = mapped_column(Integer, nullable=False); awarded_by: Mapped[str] = mapped_column(String(200), nullable=False); reason: Mapped[str] = mapped_column(Text, nullable=False, default=""); submission_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True); event_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True); detail: Mapped[str] = mapped_column(Text, nullable=False, default=""); created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
class MemberProfile(Base):
    __tablename__ = "member_profiles"
    user_id: Mapped[str] = mapped_column(String(32), primary_key=True); muid: Mapped[str] = mapped_column(String(100), nullable=False); display_name: Mapped[str] = mapped_column(String(200), nullable=False); updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

def initialize() -> None: Base.metadata.create_all(engine)
def create_submission(**values: Any) -> Submission | None:
    with Session(engine) as s:
        if s.scalar(select(Submission.id).where(Submission.attachment_id == values["attachment_id"])): return None
        row = Submission(**values); s.add(row)
        try: s.commit()
        except IntegrityError: s.rollback(); return None
        s.refresh(row); return row
def set_review_message(submission_id: int, message_id: int) -> None:
    with Session(engine) as s:
        row=s.get(Submission, submission_id)
        if row: row.review_message_id=str(message_id); s.commit()
def resolve_review(submission_id: int, reviewer_id: int, approved: bool, points: int, reviewer: str) -> tuple[bool, int]:
    with Session(engine) as s:
        row=s.get(Submission, submission_id)
        if not row or row.review_status != "pending": return False, 0
        row.review_status="approved" if approved else "rejected"; row.reviewer_id=str(reviewer_id); row.reviewed_at=utcnow()
        if approved: s.add(KarmaLedger(user_id=row.user_id,user_display=row.user_display,points=points,awarded_by=reviewer,reason=f"NASA Mons review: {row.filename}",submission_id=row.id))
        s.commit(); return True, int(s.scalar(select(func.coalesce(func.sum(KarmaLedger.points),0)).where(KarmaLedger.user_id==row.user_id)) or 0)
def award_karma(user_id:int,user_display:str,points:int,awarded_by:str,reason:str)->int:
    with Session(engine) as s:
        s.add(KarmaLedger(user_id=str(user_id),user_display=user_display,points=points,awarded_by=awarded_by,reason=reason)); s.commit(); return int(s.scalar(select(func.coalesce(func.sum(KarmaLedger.points),0)).where(KarmaLedger.user_id==str(user_id))) or 0)
def karma_total(user_id:int)->int:
    with Session(engine) as s: return int(s.scalar(select(func.coalesce(func.sum(KarmaLedger.points),0)).where(KarmaLedger.user_id==str(user_id))) or 0)
def pending_submissions()->list[tuple[int,int,str,int]]:
    with Session(engine) as s:
        rows=s.execute(select(Submission.id,Submission.user_id,Submission.filename,Submission.result_json).where(Submission.review_status=="pending")).all()
        output=[]
        for row in rows:
            try: points=int(json.loads(row[3]).get("points",100))
            except (ValueError, TypeError, json.JSONDecodeError): points=100
            output.append((int(row[0]),int(row[1]),str(row[2]),points))
        return output
def metrics()->dict[str,int]:
    with Session(engine) as s: return {"submissions":int(s.scalar(select(func.count(Submission.id))) or 0),"pending_reviews":int(s.scalar(select(func.count(Submission.id)).where(Submission.review_status=="pending")) or 0),"approved_reviews":int(s.scalar(select(func.count(Submission.id)).where(Submission.review_status=="approved")) or 0)}
def audit(event_type:str,detail:str="")->None:
    with Session(engine) as s: s.add(AuditEvent(event_type=event_type,detail=detail[:4000])); s.commit()
def save_profile(user_id:int,muid:str,display_name:str)->None:
    with Session(engine) as s:
        row=s.get(MemberProfile,str(user_id))
        if row: row.muid=muid; row.display_name=display_name; row.updated_at=utcnow()
        else: s.add(MemberProfile(user_id=str(user_id),muid=muid,display_name=display_name))
        s.commit()
def get_profile_muid(user_id:int)->str:
    with Session(engine) as s:
        row=s.get(MemberProfile,str(user_id)); return row.muid if row else ""
