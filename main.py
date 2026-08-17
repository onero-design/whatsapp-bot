import os
import json
import urllib.request
from datetime import datetime
from fastapi import FastAPI, Form, Response
from twilio.twiml.messaging_response import MessagingResponse
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

# --- CONFIGURAZIONE DATABASE E GEMINI ---
DATABASE_URL = os.getenv("DATABASE_URL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- MODELLI DATABASE ---
class Contatto(Base):
    __tablename__ = "contatti"
    id = Column(Integer, primary_key=True, index=True)
    numero_whatsapp = Column(String, unique=True, index=True, nullable=False)
    stato = Column(String, default="Nuovo Lead")
    creato_il = Column(DateTime, default=datetime.utcnow)
    messaggi = relationship("Messaggio", back_populates="contatto")

class Messaggio(Base):
    __tablename__ = "messaggi"
    id = Column(Integer, primary_key=True, index=True)
    contatto_id = Column(Integer, ForeignKey("contatti.id"))
    direzione = Column(String)  # 'INBOUND' o 'OUTBOUND'
    testo = Column(Text, nullable=False)
    inviato_il = Column(DateTime, default=datetime.utcnow)
    contatto = relationship("Contatto", back_populates="messaggi")

Base.metadata.create_all(bind=engine)

# --- CHIAMATA IA CORRETTA ---
def genera_risposta_gemini(messaggio_utente: str) -> str:
    if not GEMINI_API_KEY:
        return "Servizio IA non disponibile (chiave API mancante)."
    
    # Endpoint corretto v1beta per gemini-1.5-flash
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    
    prompt = (
        "Sei un assistente virtuale professionale e cordiale per un'azienda. "
        "Rispondi in modo sintetico, chiaro e cortese ai clienti su WhatsApp. "
        f"Messaggio del cliente: {messaggio_utente}"
    )
    
    payload = {
        "contents": [{
            "parts": [{"text": prompt}]
        }]
    }
    
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, 
        data=data, 
        headers={"Content-Type": "application/json"}
    )
    
    try:
        with urllib.request.urlopen(req) as response:
            res_body = json.loads(response.read().decode("utf-8"))
            return res_body["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        print(f"Errore Gemini: {e}")
        return "Grazie per il messaggio! Un nostro operatore ti risponderà al più presto."

# --- FASTAPI APP ---
app = FastAPI()

@app.get("/")
def home():
    return {"status": "ok", "message": "WhatsApp Bot Attivo"}

@app.post("/whatsapp-webhook")
def whatsapp_webhook(From: str = Form(...), Body: str = Form(...)):
    db = SessionLocal()
    try:
        contatto = db.query(Contatto).filter(Contatto.numero_whatsapp == From).first()
        if not contatto:
            contatto = Contatto(numero_whatsapp=From)
            db.add(contatto)
            db.commit()
            db.refresh(contatto)

        db.add(Messaggio(contatto_id=contatto.id, direzione="INBOUND", testo=Body))
        db.commit()

        risposta_testo = genera_risposta_gemini(Body)

        db.add(Messaggio(contatto_id=contatto.id, direzione="OUTBOUND", testo=risposta_testo))
        db.commit()

    finally:
        db.close()

    resp = MessagingResponse()
    resp.message(risposta_testo)
    return Response(content=str(resp), media_type="application/xml")
