import os
import json
import urllib.request
from datetime import datetime
from fastapi import FastAPI, Form, Response
from twilio.twiml.messaging_response import MessagingResponse
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

# --- CONFIGURAZIONE DATABASE ---
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

# --- FUNZIONE CHIAMATA IA GEMINI ---
def genera_risposta_gemini(messaggio_utente: str) -> str:
    if not GEMINI_API_KEY:
        return "Servizio IA temporaneamente non disponibile (chiave API mancante)."
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    
    prompt_sistema = (
        "Sei un assistente virtuale professionale e cordiale per un'azienda. "
        "Rispondi in modo sintetico, chiaro e utile ai clienti su WhatsApp. "
        "Ecco il messaggio del cliente: "
    )
    
    payload = {
        "contents": [{
            "parts": [{"text": prompt_sistema + messaggio_utente}]
        }]
    }
    
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    
    try:
        with urllib.request.urlopen(req) as response:
            res_body = json.loads(response.read().decode("utf-8"))
            return res_body["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        return "Grazie per averci scritto! Un nostro operatore ti risponderà al più presto."

# --- FASTAPI ---
app = FastAPI()

@app.get("/")
def home():
    return {"status": "ok", "message": "WhatsApp AI Bot attivo"}

@app.get("/contatti-salvati")
def leggi_contatti():
    db = SessionLocal()
    try:
        contatti = db.query(Contatto).all()
        esito = []
        for c in contatti:
            esito.append({
                "id": c.id,
                "numero": c.numero_whatsapp,
                "stato": c.stato,
                "data_creazione": c.creato_il
            })
        return {"totale": len(esito), "contatti": esito}
    finally:
        db.close()

@app.post("/whatsapp-webhook")
def whatsapp_webhook(From: str = Form(...), Body: str = Form(...)):
    db = SessionLocal()
    try:
        # 1. Trova o crea contatto
        contatto = db.query(Contatto).filter(Contatto.numero_whatsapp == From).first()
        if not contatto:
            contatto = Contatto(numero_whatsapp=From)
            db.add(contatto)
            db.commit()
            db.refresh(contatto)

        # 2. Salva messaggio utente
        db.add(Messaggio(contatto_id=contatto.id, direzione="INBOUND", testo=Body))
        db.commit()

        # 3. Genera risposta con Gemini IA
        risposta_testo = genera_risposta_gemini(Body)

        # 4. Salva risposta del bot
        db.add(Messaggio(contatto_id=contatto.id, direzione="OUTBOUND", testo=risposta_testo))
        db.commit()

    finally:
        db.close()

    # 5. Invia a WhatsApp
    resp = MessagingResponse()
    resp.message(risposta_testo)
    return Response(content=str(resp), media_type="application/xml")
