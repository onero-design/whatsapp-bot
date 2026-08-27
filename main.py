import os
from datetime import datetime, timedelta
from fastapi import FastAPI, Form, Response, Depends, BackgroundTasks, HTTPException
from pydantic import BaseModel, EmailStr
from mailer import send_email
from fastapi.responses import HTMLResponse
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client as TwilioClient
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Text, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session
from apscheduler.schedulers.background import BackgroundScheduler

from ai_service import genera_risposta_gemini, genera_bozza_email_b2b, trova_email_dominio_ia
from dashboard import get_dashboard_routes
from instagram import get_instagram_routes

# --- CONFIGURAZIONE DATABASE ---
DATABASE_URL = os.getenv("DATABASE_URL")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")

if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- MODELLI DATABASE ---
class Azienda(Base):
    __tablename__ = "aziende"
    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String, nullable=False)
    numero_whatsapp_business = Column(String, unique=True, index=True)
    istruzioni_ia = Column(Text, nullable=False)
    creato_il = Column(DateTime, default=datetime.utcnow)
    
    contatti = relationship("Contatto", back_populates="azienda")
    slot = relationship("SlotAgenda", back_populates="azienda")

class Contatto(Base):
    __tablename__ = "contatti"
    id = Column(Integer, primary_key=True, index=True)
    azienda_id = Column(Integer, ForeignKey("aziende.id"))
    numero_whatsapp = Column(String, index=True, nullable=False)
    stato = Column(String, default="Nuovo Lead")
    creato_il = Column(DateTime, default=datetime.utcnow)
    
    azienda = relationship("Azienda", back_populates="contatti")
    messaggi = relationship("Messaggio", back_populates="contatto")

class Messaggio(Base):
    __tablename__ = "messaggi"
    id = Column(Integer, primary_key=True, index=True)
    contatto_id = Column(Integer, ForeignKey("contatti.id"))
    direzione = Column(String)
    testo = Column(Text, nullable=False)
    inviato_il = Column(DateTime, default=datetime.utcnow)
    
    contatto = relationship("Contatto", back_populates="messaggi")

class SlotAgenda(Base):
    __tablename__ = "slot_agenda"
    id = Column(Integer, primary_key=True, index=True)
    azienda_id = Column(Integer, ForeignKey("aziende.id"))
    data_ora = Column(String, nullable=False, index=True)
    stato = Column(String, default="Disponibile")
    cliente_nome = Column(String, nullable=True)
    numero_cliente = Column(String, nullable=True)
    servizio = Column(String, nullable=True)
    notifica_inviata = Column(Boolean, default=False)
    
    azienda = relationship("Azienda", back_populates="slot")

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- PROMEMORIA AUTOMATICI ---
def invia_promemoria_automatici():
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        return

    db = SessionLocal()
    try:
        ora_corrente = datetime.now()
        prossima_finestra = ora_corrente + timedelta(hours=2)
        
        appuntamenti_da_notificare = db.query(SlotAgenda).filter(
            SlotAgenda.stato == "Occupato",
            SlotAgenda.notifica_inviata == False
        ).all()

        twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

        for slot in appuntamenti_da_notificare:
            try:
                data_appuntamento = datetime.strptime(slot.data_ora, "%Y-%m-%d %H:%M")
                if ora_corrente < data_appuntamento <= prossima_finestra and slot.numero_cliente:
                    azienda = db.query(Azienda).filter(Azienda.id == slot.azienda_id).first()
                    
                    messaggio_txt = (
                        f"Ciao {slot.cliente_nome}! Ti ricordiamo il tuo appuntamento "
                        f"per '{slot.servizio}' oggi alle {data_appuntamento.strftime('%H:%M')} presso {azienda.nome}. A presto!"
                    )
                    
                    twilio_client.messages.create(
                        body=messaggio_txt,
                        from_=azienda.numero_whatsapp_business,
                        to=slot.numero_cliente
                    )
                    
                    slot.notifica_inviata = True
                    db.commit()
            except Exception as e:
                print(f"Errore slot {slot.id}: {e}")
    finally:
        db.close()

# --- FASTAPI APP & ROUTERS ---
app = FastAPI()

# Collega i moduli di Dashboard e Instagram
app.include_router(get_dashboard_routes(get_db, Azienda, SlotAgenda))
app.include_router(get_instagram_routes(get_db, Azienda, Contatto, Messaggio, SlotAgenda))

# Schedulatore promemoria
scheduler = BackgroundScheduler()
scheduler.add_job(invia_promemoria_automatici, 'interval', minutes=15)
scheduler.start()

@app.get("/", response_class=HTMLResponse)
def home(db: Session = Depends(get_db)):
    azienda = db.query(Azienda).first()
    if azienda:
        return f"<h2>Bot Attivo! <a href='/dashboard/{azienda.id}'>Accedi alla Dashboard</a></h2>"
    return "<h2>Bot Attivo! Invia prima un messaggio per configurare l'azienda.</h2>"

# --- WEBHOOK WHATSAPP (TWILIO) ---
@app.post("/whatsapp-webhook")
async def whatsapp_webhook(From: str = Form(...), To: str = Form(...), Body: str = Form(...), db: Session = Depends(get_db)):
    numero_cliente = From
    numero_business = To
    messaggio_utente = Body.strip()

    azienda = db.query(Azienda).filter(Azienda.numero_whatsapp_business == numero_business).first()
    if not azienda:
        istruzioni_default = (
            "Sei l'assistente della Pasticceria.\n"
            "Servizi: Torte 1kg (10€), Torte 2kg (15€), Cornetti (1.50€).\n"
            "Orari: Lun-Sab dalle 7:00 alle 19:00.\n"
            "Regola: Quando chiedono di prenotare, verifica la disponibilità e chiedi il nome."
        )
        azienda = Azienda(
            nome="Pasticceria Demo",
            numero_whatsapp_business=numero_business,
            istruzioni_ia=istruzioni_default
        )
        db.add(azienda)
        db.commit()
        db.refresh(azienda)

    contatto = db.query(Contatto).filter(
        Contatto.numero_whatsapp == numero_cliente,
        Contatto.azienda_id == azienda.id
    ).first()

    if not contatto:
        contatto = Contatto(numero_whatsapp=numero_cliente, azienda_id=azienda.id)
        db.add(contatto)
        db.commit()
        db.refresh(contatto)

    db.add(Messaggio(contatto_id=contatto.id, direzione="INBOUND", testo=messaggio_utente))
    db.commit()

    risposta_ia = genera_risposta_gemini(azienda, contatto, messaggio_utente, db, SlotAgenda, Messaggio)

    db.add(Messaggio(contatto_id=contatto.id, direzione="OUTBOUND", testo=risposta_ia))
    db.commit()

    resp = MessagingResponse()
    resp.message(risposta_ia)
    return Response(content=str(resp), media_type="application/xml")
    
    
class EmailSchema(BaseModel):
    to_email: EmailStr
    subject: str
    body: str

@app.post("/send-mail/")
async def send_mail_endpoint(payload: EmailSchema, background_tasks: BackgroundTasks):
    background_tasks.add_task(send_email, payload.to_email, payload.subject, payload.body)
    return {"status": "success", "message": "Email presa in carico e in fase di invio."}

# --- GENERATORE DI BOZZE EMAIL B2B CON IA ---
class DraftEmailRequest(BaseModel):
    target_info: str
    offerta_azienda: str

@app.post("/api/generate-email-draft")
async def generate_email_draft(data: DraftEmailRequest):
    result = genera_bozza_email_b2b(
        target_info=data.target_info,
        offerta_azienda=data.offerta_azienda
    )
    return result

# --- ROTTA RICERCA EMAIL DOMINIO CON IA ---
class DomainSearchRequest(BaseModel):
    domain: str

@app.post("/api/find-domain-emails")
async def find_domain_emails_endpoint(data: DomainSearchRequest):
    return trova_email_dominio_ia(data.domain)
