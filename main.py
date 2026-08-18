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
class Azienda(Base):
    __tablename__ = "aziende"
    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String, nullable=False)
    numero_whatsapp_business = Column(String, unique=True, index=True)
    istruzioni_ia = Column(Text, nullable=False)  # Prompt personalizzato (orari, servizi, regole)
    creato_il = Column(DateTime, default=datetime.utcnow)
    
    contatti = relationship("Contatto", back_populates="azienda")

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
    direzione = Column(String)  # 'INBOUND' o 'OUTBOUND'
    testo = Column(Text, nullable=False)
    inviato_il = Column(DateTime, default=datetime.utcnow)
    
    contatto = relationship("Contatto", back_populates="messaggi")

Base.metadata.create_all(bind=engine)

# --- DIPENDENZA DATABASE ---
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- CHIAMATA IA DINAMICA ---
def genera_risposta_gemini(azienda: Azienda, contatto: Contatto, messaggio_attuale: str, db_session: Session) -> str:
    if not client:
        return "Servizio IA temporaneamente non disponibile."

    # Recupera gli ultimi 6 messaggi dello specifico contatto
    storico = db_session.query(Messaggio).filter(
        Messaggio.contatto_id == contatto.id
    ).order_by(Messaggio.inviato_il.desc()).limit(6).all()
    
    storico.reverse()

    conversazione = ""
    for msg in storico:
        ruolo = "Cliente" if msg.direzione == "INBOUND" else "Assistente"
        conversazione += f"{ruolo}: {msg.testo}\n"
    
    prompt = (
        f"Sei l'assistente virtuale di {azienda.nome}.\n"
        f"ISTRUZIONI E REGOLE AZIENDALI:\n{azienda.istruzioni_ia}\n\n"
        f"--- CRONOLOGIA CHAT ---\n{conversazione}"
        f"Cliente: {messaggio_attuale}\n"
        "Assistente:"
    )

    try:
        response = client.models.generate_content(
            model="gemini-3.5-flash",
            contents=prompt,
        )
        return response.text.strip()
    except Exception as e:
        print(f"Errore Gemini 3.5 Flash: {e}")
        try:
            response = client.models.generate_content(
                model="gemini-3.5-flash-lite",
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
    return {"status": "ok", "message": "WhatsApp Bot Multi-Tenant Attivo"}

# --- WEBHOOK TWILIO ---
@app.post("/whatsapp-webhook")
async def whatsapp_webhook(From: str = Form(...), To: str = Form(...), Body: str = Form(...), db: Session = Depends(get_db)):
    numero_cliente = From
    numero_business = To
    messaggio_utente = Body.strip()

    # 1. Trova l'azienda associata al numero di WhatsApp Business ricevente
    azienda = db.query(Azienda).filter(Azienda.numero_whatsapp_business == numero_business).first()
    
    # Se l'azienda non esiste ancora nel DB, ne creiamo una di default per i test
    if not azienda:
        istruzioni_default = (
            "Servizi: Taglio uomo (20€), Barba (15€), Taglio+Barba (30€).\n"
            "Orari: Mar-Sab dalle 9:00 alle 19:00.\n"
            "Indirizzo: Via Roma 10, Milano.\n"
            "Regola: Sii sempre cordiale, rispondi alle domande e proponi di fissare un appuntamento."
        )
        azienda = Azienda(
            nome="Barberia Demo",
            numero_whatsapp_business=numero_business,
            istruzioni_ia=istruzioni_default
        )
        db.add(azienda)
        db.commit()
        db.refresh(azienda)

    # 2. Recupera o crea il contatto per questa specifica azienda
    contatto = db.query(Contatto).filter(
        Contatto.numero_whatsapp == numero_cliente,
        Contatto.azienda_id == azienda.id
    ).first()

    if not contatto:
        contatto = Contatto(numero_whatsapp=numero_cliente, azienda_id=azienda.id)
        db.add(contatto)
        db.commit()
        db.refresh(contatto)

    # 3. Salva messaggio in entrata
    msg_user = Messaggio(contatto_id=contatto.id, direzione="INBOUND", testo=messaggio_utente)
    db.add(msg_user)
    db.commit()

    # 4. Genera risposta dinamica
    risposta_ia = genera_risposta_gemini(azienda, contatto, messaggio_utente, db)

    # 5. Salva risposta in uscita
    msg_bot = Messaggio(contatto_id=contatto.id, direzione="OUTBOUND", testo=risposta_ia)
    db.add(msg_bot)
    db.commit()

    resp = MessagingResponse()
    resp.message(risposta_ia)
    return Response(content=str(resp), media_type="application/xml")
