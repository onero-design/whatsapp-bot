import os
import requests
from datetime import datetime, timedelta
from fastapi import FastAPI, Form, Response, Depends, BackgroundTasks, HTTPException, Request, status
from pydantic import BaseModel, EmailStr
from mailer import send_email
from fastapi.responses import HTMLResponse, RedirectResponse
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client as TwilioClient
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Text, Boolean, text
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session
from apscheduler.schedulers.background import BackgroundScheduler
from passlib.context import CryptContext
from jinja2 import Template

from ai_service import genera_risposta_gemini, genera_bozza_email_b2b, trova_email_dominio_ia
from dashboard import get_dashboard_routes
from instagram import get_instagram_routes
from admin_dashboard import get_admin_routes

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def genera_hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verifica_password(password_chiara: str, password_hash: str) -> bool:
    return pwd_context.verify(password_chiara, password_hash)

# --- CONFIGURAZIONE DATABASE & VARIABILI D'AMBIENTE ---
DATABASE_URL = os.getenv("DATABASE_URL")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI")

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
    
    # Campi integrati per Google Calendar OAuth2
    google_access_token = Column(Text, nullable=True)
    google_refresh_token = Column(Text, nullable=True)
    google_calendar_id = Column(String, nullable=True, default="primary")
    
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
    # --- NUOVI CAMPI PER LA GESTIONE SAAS ---
    is_active = Column(Boolean, default=True)   # True = Attivo, False = Disabilitato (non paga)
    is_admin = Column(Boolean, default=False)   # True solo per la TUA email personale

    azienda = relationship("Azienda", back_populates="utenti")

# Allineamento automatico colonne nel DB
try:
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE aziende ADD COLUMN IF NOT EXISTS google_access_token TEXT;"))
        conn.execute(text("ALTER TABLE aziende ADD COLUMN IF NOT EXISTS google_refresh_token TEXT;"))
        conn.execute(text("ALTER TABLE aziende ADD COLUMN IF NOT EXISTS google_calendar_id VARCHAR DEFAULT 'primary';"))
        
        # Migrazioni per Utente
        conn.execute(text("ALTER TABLE utenti ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;"))
        conn.execute(text("ALTER TABLE utenti ADD COLUMN IF NOT EXISTS is_admin BOOLEAN DEFAULT FALSE;"))
        conn.commit()
except Exception as e:
    print(f"Errore durante la migrazione del DB: {e}")

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- HELPER PARSING DATA FLOTTANTE ---
def parse_date_string(date_str: str) -> datetime:
    clean_str = str(date_str).strip().replace("Z", "")
    try:
        return datetime.fromisoformat(clean_str)
    except ValueError:
        formats = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"]
        for fmt in formats:
            try:
                return datetime.strptime(clean_str, fmt)
            except ValueError:
                continue
        raise ValueError(f"Impossibile formattare la data: {date_str}")

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
                data_appuntamento = parse_date_string(slot.data_ora)
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
app.include_router(get_dashboard_routes(get_db, Azienda))
app.include_router(get_instagram_routes(get_db, Azienda, Contatto, Messaggio, SlotAgenda))
app.include_router(get_admin_routes(get_db, Azienda, Utente, genera_hash_password))

# Schedulatore promemoria
scheduler = BackgroundScheduler()
scheduler.add_job(invia_promemoria_automatici, 'interval', minutes=15)
scheduler.start()

# --- ROTTE OAUTH2 GOOGLE CALENDAR ---

@app.get("/auth/google/login")
def google_login(azienda_id: int):
    """Avvia il flusso OAuth2 per collegare Google Calendar."""
    if not GOOGLE_CLIENT_ID or not GOOGLE_REDIRECT_URI:
        raise HTTPException(status_code=500, detail="Credenziali GOOGLE_CLIENT_ID o GOOGLE_REDIRECT_URI non configurate su Render.")
        
    google_auth_url = (
        "https://accounts.google.com/o/oauth2/v2/auth?"
        f"client_id={GOOGLE_CLIENT_ID}&"
        f"redirect_uri={GOOGLE_REDIRECT_URI}&"
        "response_type=code&"
        "scope=https://www.googleapis.com/auth/calendar.events&"
        "access_type=offline&"
        "prompt=consent&"
        f"state={azienda_id}"
    )
    return RedirectResponse(google_auth_url)

@app.get("/auth/google/callback")
def google_callback(code: str, state: str, db: Session = Depends(get_db)):
    """Riceve il codice di autorizzazione da Google e salva i token nel DB."""
    azienda_id = int(state)
    
    token_url = "https://oauth2.googleapis.com/token"
    payload = {
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": GOOGLE_REDIRECT_URI,
    }
    
    response = requests.post(token_url, data=payload)
    res_data = response.json()
    
    if "error" in res_data:
        raise HTTPException(status_code=400, detail=f"Errore Google OAuth: {res_data.get('error_description')}")

    access_token = res_data.get("access_token")
    refresh_token = res_data.get("refresh_token")

    azienda = db.query(Azienda).filter(Azienda.id == azienda_id).first()
    if azienda:
        azienda.google_access_token = access_token
        if refresh_token:
            azienda.google_refresh_token = refresh_token
        db.commit()

    return {"status": "success", "message": f"Google Calendar collegato con successo per l'azienda ID: {azienda_id}!"}

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
    # CHECK BLOCCO UTENTE (Se non paga o è disattivato)
    if not utente.is_active:
        with open("login.html", "r", encoding="utf-8") as f:
            template = Template(f.read())
        return HTMLResponse(
            content=template.render(request=request, errore="Account sospeso. Contatta l'amministratore."),
            status_code=403
        )

    # Se sei l'Admin va alla Super Dashboard, altrimenti alla dashboard del cliente
    if utente.is_admin:
        response = RedirectResponse(url="/admin/super-dashboard", status_code=status.HTTP_303_SEE_OTHER)
    else:
        response = RedirectResponse(url=f"/dashboard/{utente.azienda_id}", status_code=status.HTTP_303_SEE_OTHER)
        
    response.set_cookie(key="azienda_id", value=str(utente.azienda_id), httponly=True)
    response.set_cookie(key="utente_id", value=str(utente.id), httponly=True)
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
    
# --- WEBHOOK WHATSAPP (TWILIO) MULTI-AZIENDA ---
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

    try:
        risposta_ia = genera_risposta_gemini(azienda, contatto, messaggio_utente, db, SlotAgenda, Messaggio)
    except Exception as e:
        print(f"Errore genera_risposta_gemini: {e}")
        risposta_ia = "Si è verificato un errore momentaneo nell'elaborazione della risposta."

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

@app.get("/aziende-list")
def lista_aziende(db: Session = Depends(get_db)):
    aziende = db.query(Azienda).all()
    return [{"id": a.id, "nome": a.nome, "numero_whatsapp": a.numero_whatsapp_business} for a in aziende]

@app.get("/imposta-numero/{azienda_id}")
def imposta_numero_sandbox(azienda_id: int, db: Session = Depends(get_db)):
    num_sandbox = "whatsapp:+14155238886"
    
    vecchia = db.query(Azienda).filter(Azienda.numero_whatsapp_business == num_sandbox).first()
    if vecchia:
        vecchia.numero_whatsapp_business = f"whatsapp:+39000000000{vecchia.id}"
        db.commit()

    target = db.query(Azienda).filter(Azienda.id == azienda_id).first()
    if not target:
        return {"status": "errore", "messaggio": "Azienda non trovata"}

    target.numero_whatsapp_business = num_sandbox
    db.commit()

    return {"status": "successo", "messaggio": f"Il numero WhatsApp di test ora risponde per: {target.nome}"}

@app.api_route("/health", methods=["GET", "HEAD"])
def health_check():
    return {"status": "ok"}



# Script una-tantum per creare l'Admin Supremo
@app.get("/setup-admin")
def setup_admin(db: Session = Depends(get_db)):
    admin_email = "andrea.onero09@gmail.com"  # Inserisci qui l'email con cui vuoi entrare
    admin_pass = "Pestifer.09"             # Inserisci qui la tua password da admin
    
    # Controlla se esiste l'azienda principale per l'admin
    azienda_admin = db.query(Azienda).filter(Azienda.nome == "SaaS Management").first()
    if not azienda_admin:
        azienda_admin = Azienda(
            nome="SaaS Management",
            numero_whatsapp_business="whatsapp:+390000000000",
            istruzioni_ia="Azienda Amministratore SaaS"
        )
        db.add(azienda_admin)
        db.commit()
        db.refresh(azienda_admin)

    # Crea o aggiorna l'utente Admin
    utente = db.query(Utente).filter(Utente.email == admin_email).first()
    if not utente:
        utente = Utente(
            email=admin_email,
            password_hash=genera_hash_password(admin_pass),
            azienda_id=azienda_admin.id,
            is_active=True,
            is_admin=True
        )
        db.add(utente)
    else:
        utente.is_admin = True
        utente.is_active = True
    
    db.commit()
    return {"status": "ok", "message": f"Admin creato con email: {admin_email}"}
