from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime

DATABASE_URL = "sqlite:///./pinpanda_beta.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

class SearchJob(Base):
    __tablename__ = "search_jobs"
    id = Column(Integer, primary_key=True)
    user_id = Column(String, index=True, nullable=False)
    keyword = Column(String)
    limit = Column(Integer)
    status = Column(String, default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)

class Lead(Base):
    __tablename__ = "leads"
    id = Column(Integer, primary_key=True)
    job_id = Column(Integer)
    shop_name = Column(String)
    pinterest_url = Column(String)
    email = Column(String)
    website = Column(String)
    platform = Column(String)
    instagram = Column(String)
    facebook = Column(String)
    tiktok = Column(String)
    youtube = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

def init_db():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()