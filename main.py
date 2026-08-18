import os
from datetime import datetime
from fastapi import FastAPI, Form, Response
from twilio.twiml.messaging_response import MessagingResponse
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
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
    direzione = Column(String)  # 'INBOUND' o 'OUTBOUND'
    testo = Column(Text, nullable=False)
    inviato_il = Column(DateTime, default=datetime.utcnow)
    contatto = relationship("Contatto", back_populates="messaggi")

Base.metadata.create_all(bind=engine)

# --- CHIAMATA IA CON MEMORIA E FALLBACK ---
def genera_risposta_gemini(numero_utente: str, messaggio_attuale: str, db_session) -> str:
    if not client:
        return "Servizio IA temporaneamente non disponibile."

    # Recupera gli ultimi 6 messaggi dello specifico utente dal DB
    storico = db_session.query(Messaggio).filter(
        Messaggio.numero_utente == numero_utente
    ).order_by(Messaggio.timestamp.desc()).limit(6).all()
    
    storico.reverse()

    conversazione = ""
    for msg in storico:
        ruolo = "Cliente" if msg.ruolo == "user" else "Assistente"
        conversazione += f"{ruolo}: {msg.testo}\n"
    
    prompt = (
        "Sei un assistente virtuale professionale per l'azienda. "
        "Rispondi al cliente in modo cordiale, chiaro e sintetico basandoti sulla cronologia della conversazione.\n\n"
        f"--- CRONOLOGIA CHAT ---\n{conversazione}"
        f"Cliente: {messaggio_attuale}\n"
        "Assistente:"
    )

    try:
        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt,
        )
        return response.text.strip()
    except Exception as e:
        print(f"Errore Gemini 3.6 Flash: {e}")
        try:
            response = client.models.generate_content(
                model="gemini-1.5-flash",
                contents=prompt,
            )
            return response.text.strip()
        except Exception as e2:
            print(f"Errore Gemini 1.5 Flash: {e2}")
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

    # Salva il messaggio dell'utente nel DB
    msg_user = Messaggio(numero_utente=numero_utente, ruolo="user", testo=messaggio_utente)
    db.add(msg_user)
    db.commit()

    # Genera la risposta usando la memoria
    risposta_ia = genera_risposta_gemini(numero_utente, messaggio_utente, db)

    # Salva la risposta dell'IA nel DB
    msg_bot = Messaggio(numero_utente=numero_utente, ruolo="assistant", testo=risposta_ia)
    db.add(msg_bot)
    db.commit()

    # Risposta formattata per Twilio TwiML
    resp = MessagingResponse()
    resp.message(risposta_ia)
    return Response(content=str(resp), media_type="application/xml")
