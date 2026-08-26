import os
import requests
from fastapi import FastAPI, HTTPException, Depends, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# Import delle funzioni dai tuoi moduli
from ai_service import genera_bozza_email_b2b
from dashboard import get_dashboard_routes
from mailer import send_email

HUNTER_API_KEY = os.getenv("HUNTER_API_KEY")

app = FastAPI(title="Email & WhatsApp Automation Bot")

# --- DATABASE SETUP ---
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./test.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Azienda(Base):
    __tablename__ = "aziende"
    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String, index=True)
    istruzioni_ia = Column(String)

class SlotAgenda(Base):
    __tablename__ = "slot_agenda"
    id = Column(Integer, primary_key=True, index=True)
    azienda_id = Column(Integer, index=True)
    data_ora = Column(String)
    cliente_nome = Column(String, nullable=True)
    servizio = Column(String, nullable=True)
    stato = Column(String, default="Occupato")

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Include le rotte della Dashboard
app.include_router(get_dashboard_routes(get_db, Azienda, SlotAgenda))

# --- MODELLI PYDANTIC ---
class DraftEmailRequest(BaseModel):
    target_info: str
    offerta_azienda: str

class AutoDomainCampaignRequest(BaseModel):
    domain: str
    subject: str
    body: str

# --- FUNZIONI UTILI ---
def cerca_email_da_dominio(domain: str) -> list[str]:
    if not HUNTER_API_KEY:
        print("HUNTER_API_KEY non configurata.")
        return []
    
    url = f"https://api.hunter.io/v2/domain-search?domain={domain}&api_key={HUNTER_API_KEY}"
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        emails = [item["value"] for item in data.get("data", {}).get("emails", [])]
        return emails
    except Exception as e:
        print(f"Errore ricerca Hunter.io: {e}")
        return []

# --- ENDPOINT API ---

@app.get("/")
def home():
    return {"status": "online", "message": "Bot attivo e pronto!"}

@app.post("/api/generate-email-draft")
async def generate_email_draft(data: DraftEmailRequest):
    result = genera_bozza_email_b2b(
        target_info=data.target_info,
        offerta_azienda=data.offerta_azienda
    )
    return result

@app.post("/api/find-domain-emails")
async def find_domain_emails(payload: dict):
    domain = payload.get("domain", "").strip()
    if not domain:
        raise HTTPException(status_code=400, detail="Dominio non valido.")
    
    emails = cerca_email_da_dominio(domain)
    return {"success": True, "domain": domain, "emails": emails, "count": len(emails)}

@app.post("/api/send-auto-domain-campaign")
async def send_auto_domain_campaign(payload: AutoDomainCampaignRequest, background_tasks: BackgroundTasks):
    emails = cerca_email_da_dominio(payload.domain)
    
    if not emails:
        raise HTTPException(status_code=404, detail=f"Nessun indirizzo email trovato per il dominio {payload.domain}.")
    
    for email in emails:
        background_tasks.add_task(send_email, email, payload.subject, payload.body)
        
    return {
        "status": "success",
        "message": f"🚀 Bot avviato! Email prese in carico e in corso di invio a {len(emails)} contatti del dominio {payload.domain}."
    }
