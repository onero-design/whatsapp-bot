import os
from datetime import datetime, timedelta
from fastapi import FastAPI, Form, Response, Depends, BackgroundTasks, HTTPException, Request, status
from pydantic import BaseModel, EmailStr
from mailer import send_email
from fastapi.responses import HTMLResponse, RedirectResponse
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client as TwilioClient
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Text, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session
from apscheduler.schedulers.background import BackgroundScheduler
from passlib.context import CryptContext
from jinja2 import Template

from ai_service import genera_risposta_gemini, genera_bozza_email_b2b, trova_email_dominio_ia
from dashboard import get_dashboard_routes
from instagram import get_instagram_routes

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def genera_hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verifica_password(password_chiara: str, password_hash: str) -> bool:
    return pwd_context.verify(password_chiara, password_hash)

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
    utenti = relationship("Utente", back_populates="azienda") 

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

class Utente(Base):
    __tablename__ = "utenti"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    azienda_id = Column(Integer, ForeignKey("aziende.id"), nullable=False)

    azienda = relationship("Azienda", back_populates="utenti")

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

# --- ROTTE DI AUTENTICAZIONE (LOGIN / LOGOUT) ---

@app.get("/login", response_class=HTMLResponse)
async def pagina_login(request: Request):
    with open("login.html", "r", encoding="utf-8") as f:
        template = Template(f.read())
    return HTMLResponse(content=template.render(request=request, errore=None))

@app.post("/login")
async def effettua_login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    utente = db.query(Utente).filter(Utente.email == email).first()

    if not utente or not verifica_password(password, utente.password_hash):
        with open("login.html", "r", encoding="utf-8") as f:
            template = Template(f.read())
        return HTMLResponse(
            content=template.render(request=request, errore="Email o password errati."),
            status_code=401
        )

    response = RedirectResponse(url=f"/dashboard/{utente.azienda_id}", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(key="azienda_id", value=str(utente.azienda_id), httponly=True)
    return response

@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(key="azienda_id")
    return response

@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    cookie_azienda = request.cookies.get("azienda_id")
    if cookie_azienda:
        return RedirectResponse(url=f"/dashboard/{cookie_azienda}", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    
@app.post("/whatsapp-webhook")
async def whatsapp_webhook(
    From: str = Form(...),
    To: str = Form(...),
    Body: str = Form(...),
    db: Session = Depends(get_db)
):
    # 1. Identifica l'azienda destinataria dal numero WhatsApp che riceve ('To')
    azienda = db.query(Azienda).filter(Azienda.numero_whatsapp_business == To).first()
    
    if not azienda:
        resp = MessagingResponse()
        resp.message("Sistema: Questo numero WhatsApp non appartiene a nessuna azienda configurata.")
        return Response(content=str(resp), media_type="application/xml")

    # 2. Prompt dinamico con il contesto dell'azienda trovata
    prompt_sistema = f"""
    {azienda.istruzioni_ia}
    
    Informazioni contesto:
    - Nome Attività: {azienda.nome}
    - Data e ora correnti: {datetime.now().strftime('%d/%m/%Y %H:%M')}
    
    Rispondi sempre in modo cortese, conciso e adatto a una conversazione WhatsApp.
    """

    # 3. Chiamata ad OpenAI
    try:
        response_ai = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": prompt_sistema},
                {"role": "user", "content": Body}
            ]
        )
        risposta_testo = response_ai.choices[0].message.content
    except Exception as e:
        print(f"ERRORE: {e}")
        risposta_testo = f"Errore rilevato: {e}"

    # 4. Risposta per Twilio
    resp = MessagingResponse()
    resp.message(risposta_testo)
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
    azienda_id: int
    target_info: str
    offerta_azienda: str

@app.post("/api/generate-email-draft")
async def generate_email_draft(data: DraftEmailRequest, db: Session = Depends(get_db)):
    azienda = db.query(Azienda).filter(Azienda.id == data.azienda_id).first()
    result = genera_bozza_email_b2b(
        azienda=azienda,
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
