import os
from datetime import datetime
from fastapi import FastAPI, Form, Response
from twilio.twiml.messaging_response import MessagingResponse
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

# --- CONFIGURAZIONE DATABASE ---
DATABASE_URL = os.getenv("DATABASE_URL")

# Render usa "postgres://", SQLAlchemy richiede "postgresql://"
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- MODELLI CRM (TABELLE) ---
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
    direzione = Column(String)  # 'INBOUND' (ricevuto) o 'OUTBOUND' (inviato dal bot)
    testo = Column(Text, nullable=False)
    inviato_il = Column(DateTime, default=datetime.utcnow)

    contatto = relationship("Contatto", back_populates="messaggi")

# Crea le tabelle nel database PostgreSQL
Base.metadata.create_all(bind=engine)

# --- APPLICAZIONE FASTAPI ---
app = FastAPI()

@app.get("/")
def home():
    return {"status": "ok", "message": "WhatsApp CRM Bot attivo con Database"}

@app.post("/whatsapp-webhook")
def whatsapp_webhook(From: str = Form(...), Body: str = Form(...)):
    db = SessionLocal()
    try:
        # 1. Cerca o crea il contatto nel CRM
        contatto = db.query(Contatto).filter(Contatto.numero_whatsapp == From).first()
        if not contatto:
            contatto = Contatto(numero_whatsapp=From)
            db.add(contatto)
            db.commit()
            db.refresh(contatto)

        # 2. Salva il messaggio ricevuto
        messaggio_in = Messaggio(
            contatto_id=contatto.id,
            direzione="INBOUND",
            testo=Body
        )
        db.add(messaggio_in)

        # 3. Logica di risposta del bot
        messaggio_utente = Body.strip().upper()
        
        if "PREZZO" in messaggio_utente:
            risposta_testo = "👋 Ciao! I nostri servizi partono da 49€/mese. Rispondi 'INFO' per parlare con un consulente!"
        elif "ORARI" in messaggio_utente:
            risposta_testo = "🕒 Siamo aperti dal lunedì al venerdì dalle 9:00 alle 18:00."
        elif "INFO" in messaggio_utente:
            risposta_testo = "Perfetto! Un nostro consulente la ricontatterà a breve su questo numero."
            contatto.stato = "Richiesta Info"
        else:
            risposta_testo = (
                "Ciao! Grazie per averci scritto. Come possiamo aiutarti oggi?\n"
                "1. Scrivi 'PREZZO' per i costi\n"
                "2. Scrivi 'ORARI' per gli orari\n"
                "3. Scrivi 'INFO' per parlare con noi"
            )

        # 4. Salva la risposta inviata dal bot
        messaggio_out = Messaggio(
            contatto_id=contatto.id,
            direzione="OUTBOUND",
            testo=risposta_testo
        )
        db.add(messaggio_out)
        db.commit()

    finally:
        db.close()

    # 5. Genera la risposta TwiML in formato XML per Twilio
    resp = MessagingResponse()
    resp.message(risposta_testo)
    return Response(content=str(resp), media_type="application/xml")
