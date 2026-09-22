import importlib
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# 数据库路径可通过环境变量 DATABASE_URL 覆盖，默认挂载到 ./data 目录
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/seating.db")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    # 确保 SQLite 文件所在目录存在
    if DATABASE_URL.startswith("sqlite:///"):
        path = DATABASE_URL.replace("sqlite:///", "", 1)
        if path and path not in (":memory:",):
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    # 触发模型注册（函数内导入，避免循环导入）
    importlib.import_module("app.models")
    Base.metadata.create_all(bind=engine)
