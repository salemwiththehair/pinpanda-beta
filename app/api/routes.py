from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from sqlalchemy.orm import Session
from app.models.database import get_db, SearchJob, Lead
from app.scraper.pinterest import run_scrape_job_sync
from pydantic import BaseModel
from typing import List
import csv
import io
from fastapi.responses import StreamingResponse
import threading
import re

router = APIRouter()
email_blocklist = set()
USER_ID = "beta_user"

class JobRequest(BaseModel):
    keywords: List[str]
    limit: int = 30
    fresh: bool = True
    emails_only: bool = False
    headless: bool = True

def extract_email_from_row(row: dict) -> str:
    email_pattern = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
    for val in row.values():
        val = str(val).strip()
        match = email_pattern.search(val)
        if match:
            return match.group(0).lower()
    return ""

@router.post("/jobs/start")
async def start_job(req: JobRequest, db: Session = Depends(get_db)):
    keyword_str = ", ".join(req.keywords)
    job = SearchJob(user_id=USER_ID, keyword=keyword_str, limit=req.limit, status="pending")
    db.add(job)
    db.commit()
    db.refresh(job)
    
    job_id = job.id
    
    # Start scraper in background thread - THIS IS THE KEY FIX
    def run_scraper():
        try:
            run_scrape_job_sync(job_id, req.keywords, req.limit, req.fresh, req.emails_only, req.headless)
        except Exception as e:
            print(f"Scraper error: {e}")
    
    thread = threading.Thread(target=run_scraper, daemon=True)
    thread.start()
    
    # Return immediately
    return {"job_id": job_id, "status": "started"}

@router.get("/jobs")
def list_jobs(db: Session = Depends(get_db)):
    jobs = db.query(SearchJob).filter(SearchJob.user_id == USER_ID).order_by(SearchJob.id.desc()).all()
    result = []
    for j in jobs:
        leads_count = db.query(Lead).filter(Lead.job_id == j.id).count()
        emails_count = db.query(Lead).filter(Lead.job_id == j.id, Lead.email != None).count()
        result.append({
            "job_id": j.id,
            "keyword": j.keyword,
            "status": j.status,
            "leads_found": leads_count,
            "emails_found": emails_count,
            "created_at": str(j.created_at),
        })
    return result

@router.get("/jobs/{job_id}/status")
def job_status(job_id: int, db: Session = Depends(get_db)):
    job = db.query(SearchJob).filter(SearchJob.id == job_id, SearchJob.user_id == USER_ID).first()
    if not job:
        raise HTTPException(404, "Job not found")
    
    leads_count = db.query(Lead).filter(Lead.job_id == job_id).count()
    emails_count = db.query(Lead).filter(Lead.job_id == job_id, Lead.email != None).count()
    
    return {
        "job_id": job_id,
        "status": job.status,
        "leads_found": leads_count,
        "emails_found": emails_count,
    }

@router.get("/jobs/{job_id}/leads")
def get_leads(job_id: int, db: Session = Depends(get_db)):
    job = db.query(SearchJob).filter(SearchJob.id == job_id, SearchJob.user_id == USER_ID).first()
    if not job:
        raise HTTPException(404, "Job not found")
    
    leads = db.query(Lead).filter(Lead.job_id == job_id).all()
    
    result = []
    for l in leads:
        result.append({
            "shop_name": l.shop_name or "",
            "email": l.email or "",
            "website": l.website or "",
            "platform": l.platform or "",
            "pinterest_url": l.pinterest_url or "",
            "instagram": l.instagram or "",
            "facebook": l.facebook or "",
            "tiktok": l.tiktok or "",
            "youtube": l.youtube or "",
        })
    return result

@router.get("/jobs/{job_id}/export")
def export_csv(job_id: int, db: Session = Depends(get_db)):
    job = db.query(SearchJob).filter(SearchJob.id == job_id, SearchJob.user_id == USER_ID).first()
    if not job:
        raise HTTPException(404, "Job not found")
    
    leads = db.query(Lead).filter(Lead.job_id == job_id).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Shop Name", "Email", "Website", "Platform", "Pinterest URL", "Instagram", "Facebook", "TikTok", "YouTube"])
    
    for l in leads:
        writer.writerow([
            l.shop_name or "",
            l.email or "",
            l.website or "",
            l.platform or "",
            l.pinterest_url or "",
            l.instagram or "",
            l.facebook or "",
            l.tiktok or "",
            l.youtube or "",
        ])
    
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]), 
        media_type="text/csv", 
        headers={"Content-Disposition": f"attachment; filename=pinpanda_leads_{job_id}.csv"}
    )

@router.post("/jobs/{job_id}/stop")
def stop_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(SearchJob).filter(SearchJob.id == job_id, SearchJob.user_id == USER_ID).first()
    if not job:
        raise HTTPException(404, "Job not found")
    job.status = "stopped"
    db.commit()
    return {"status": "stopped"}

@router.delete("/jobs/{job_id}")
def delete_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(SearchJob).filter(SearchJob.id == job_id, SearchJob.user_id == USER_ID).first()
    if not job:
        raise HTTPException(404, "Job not found")
    
    db.query(Lead).filter(Lead.job_id == job_id).delete()
    db.query(SearchJob).filter(SearchJob.id == job_id).delete()
    db.commit()
    return {"status": "deleted"}

@router.post("/blocklist/upload")
async def upload_blocklist(file: UploadFile = File(...)):
    global email_blocklist
    content = await file.read()
    try:
        decoded = content.decode("utf-8")
    except:
        decoded = content.decode("latin-1", errors="ignore")
    
    sample = decoded[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except:
        dialect = csv.excel
    
    reader = csv.DictReader(io.StringIO(decoded), dialect=dialect)
    added = 0
    for row in reader:
        email = extract_email_from_row(row)
        if email:
            email_blocklist.add(email)
            added += 1
    
    return {
        "status": "ok", 
        "emails_blocked": len(email_blocklist), 
        "added": added
    }

@router.get("/blocklist/count")
def blocklist_count():
    return {"count": len(email_blocklist)}

@router.delete("/blocklist/clear")
def clear_blocklist():
    global email_blocklist
    email_blocklist.clear()
    return {"status": "cleared"}