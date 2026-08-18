import os
from datetime import datetime
from fastapi import FastAPI, Form, Response, Depends
from twilio.twiml.messaging_response import MessagingResponse
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session
from google import genai

# --- CONFIGURAZIONE DATABASE E GEMINI ---
DATABASE_URL = os.getenv("DATABASE_URL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- INIZIALIZZAZIONE CLIENT GEMINI ---
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

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
    direzione = Column(String)  # 'INBOUND' (cliente) o 'OUTBOUND' (bot)
    testo = Column(Text, nullable=False)
    inviato_il = Column(DateTime, default=datetime.utcnow)
    contatto = relationship("Contatto", back_populates="messaggi")

Base.metadata.create_all(bind=engine)

# --- DIPENDENZA DATABASE PER FASTAPI ---
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- CHIAMATA IA CON MEMORIA E FALLBACK ---
def genera_risposta_gemini(contatto: Contatto, messaggio_attuale: str, db_session: Session) -> str:
    if not client:
        return "Servizio IA temporaneamente non disponibile."

    storico = db_session.query(Messaggio).filter(
        Messaggio.contatto_id == contatto.id
    ).order_by(Messaggio.inviato_il.desc()).limit(6).all()
    
    storico.reverse()

    conversazione = ""
    for msg in storico:
        ruolo = "Cliente" if msg.direzione == "INBOUND" else "Assistente"
        conversazione += f"{ruolo}: {msg.testo}\n"
    
    prompt = (
        "Sei un assistente virtuale professionale per l'azienda. "
        "Rispondi al cliente in modo cordiale, chiaro e sintetico basandoti sulla cronologia della conversazione.\n\n"
        f"--- CRONOLOGIA CHAT ---\n{conversazione}"
        f"Cliente: {messaggio_attuale}\n"
        "Assistente:"
    )

    # 1. Tentativo principale con Gemini 1.5 Flash
    try:
        response = client.models.generate_content(
            model="gemini-1.5-flash",
            contents=prompt,
        )
        return response.text.strip()
    except Exception as e:
        print(f"Errore Gemini 1.5 Flash: {e}")
        # 2. Fallback su Gemini 1.5 Flash 8B (ancora più veloce)
        try:
            response = client.models.generate_content(
                model="gemini-1.5-flash-8b",
                contents=prompt,
            )
            return response.text.strip()
        except Exception as e2:
            print(f"Errore Fallback: {e2}")
            return "Grazie per il messaggio! Un operatore ti risponderà a breve."
# --- FASTAPI APP ---
app = FastAPI()

@app.get("/")
def home():
    return {"status": "ok", "message": "WhatsApp Bot Attivo"}

# --- WEBHOOK TWILIO ---
@app.post("/whatsapp-webhook")
async def whatsapp_webhook(From: str = Form(...), Body: str = Form(...), db: Session = Depends(get_db)):
    numero_utente = From
    messaggio_utente = Body.strip()

    # Recupera o crea il contatto
    contatto = db.query(Contatto).filter(Contatto.numero_whatsapp == numero_utente).first()
    if not contatto:
        contatto = Contatto(numero_whatsapp=numero_utente)
        db.add(contatto)
        db.commit()
        db.refresh(contatto)

    # Salva il messaggio in entrata
    msg_user = Messaggio(contatto_id=contatto.id, direzione="INBOUND", testo=messaggio_utente)
    db.add(msg_user)
    db.commit()

    # Genera la risposta dell'IA con la memoria dei messaggi precedenti
    risposta_ia = genera_risposta_gemini(contatto, messaggio_utente, db)

    # Salva la risposta in uscita
    msg_bot = Messaggio(contatto_id=contatto.id, direzione="OUTBOUND", testo=risposta_ia)
    db.add(msg_bot)
    db.commit()

    # Risposta formattata per Twilio
    resp = MessagingResponse()
    resp.message(risposta_ia)
    return Response(content=str(resp), media_type="application/xml")
