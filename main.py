from fastapi import FastAPI, Depends, Form, HTTPException, Response
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
import datetime

# --- CONFIGURAZIONE DATABASE (SQLite) ---
DATABASE_URL = "sqlite:///./contatti.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class ContattoDB(Base):
    __tablename__ = "contatti"

    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String, nullable=False)
    telefono = Column(String, nullable=False)
    data_creazione = Column(DateTime, default=datetime.datetime.utcnow)

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- CONFIGURAZIONE FASTAPI E TWILIO ---
app = FastAPI(title="Backend WhatsApp & Webhook Auto-Responder")

TWILIO_ACCOUNT_SID = "ACc9e1f12f5ee591f0e66edffeaac0a9a0"
TWILIO_AUTH_TOKEN = "4996d8e14d4abd4c237c023718b57492"
MITTENTE_TWILIO = "whatsapp:+4915888623971"

client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)


# --- WEBHOOK PER MESSAGGI IN ARRIVO (CHATBOT AUTOMATICO) ---
@app.post("/whatsapp-webhook")
async def whatsapp_webhook(
    From: str = Form(...),
    Body: str = Form(...),
    db: Session = Depends(get_db)
):
    # pulizia del numero mittente (es. "whatsapp:+393331234567" -> "+393331234567")
    numero_mittente = From.replace("whatsapp:", "").strip()
    testo_ricevuto = Body.strip().lower()

    print(f"📩 Messaggio ricevuto da {numero_mittente}: {Body}")

    # 1. Cerca o crea il contatto nel Database
    contatto = db.query(ContattoDB).filter(ContattoDB.telefono == numero_mittente).first()
    if not contatto:
        contatto = ContattoDB(nome="Cliente WhatsApp", telefono=numero_mittente)
        db.add(contatto)
        db.commit()
        db.refresh(contatto)

    # 2. Logica di risposta automatica del Chatbot
    resp = MessagingResponse()

    if "prezzo" in testo_ricevuto or "costo" in testo_ricevuto or "quanto" in testo_ricevuto:
        risposta_testo = "👋 Ciao! I nostri servizi partono da 49€/mese. Rispondi 'INFO' per parlare con un consulente!"
    elif "info" in testo_ricevuto or "consulente" in testo_ricevuto:
        risposta_testo = f"Perfetto! Un nostro consulente la ricontatterà a breve su questo numero."
    elif "orari" in testo_ricevuto or "aperti" in testo_ricevuto:
        risposta_testo = "📍 Siamo aperti dal Lunedì al Venerdì dalle 9:00 alle 18:00."
    else:
        risposta_testo = f"Ciao! Grazie per averci scritto. Come possiamo aiutarti oggi?\n1. Scrivi 'PREZZO' per i costi\n2. Scrivi 'ORARI' per gli orari\n3. Scrivi 'INFO' per parlare con noi"

    # Genera la risposta TWiML per Twilio
    resp.message(risposta_testo)
    return Response(content=str(resp), media_type="application/xml")


# --- ENDPOINT CONSULTAZIONE CONTATTI ---
@app.get("/contatti")
def lista_contatti(db: Session = Depends(get_db)):
    contatti = db.query(ContattoDB).all()
    return {"totale": len(contatti), "contatti": contatti}
