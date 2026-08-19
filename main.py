import os
from datetime import datetime
from fastapi import FastAPI, Form, Response, Depends
from twilio.twiml.messaging_response import MessagingResponse
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session
from google import genai
from google.genai import types

# --- CONFIGURAZIONE DATABASE E GEMINI ---
DATABASE_URL = os.getenv("DATABASE_URL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

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
    data_ora = Column(String, nullable=False, index=True) # Formato YYYY-MM-DD HH:MM
    stato = Column(String, default="Disponibile") # 'Disponibile', 'Occupato'
    cliente_nome = Column(String, nullable=True)
    servizio = Column(String, nullable=True)
    
    azienda = relationship("Azienda", back_populates="slot")

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- FUNZIONI STRUMENTO (TOOLS PER GEMINI) ---
def cerca_slot_disponibile(azienda_id: int, data_ora: str, db: Session) -> str:
    slot = db.query(SlotAgenda).filter(
        SlotAgenda.azienda_id == azienda_id,
        SlotAgenda.data_ora == data_ora
    ).first()
    
    if not slot or slot.stato == "Disponibile":
        return f"Lo slot per il {data_ora} è DISPONIBILE."
    return f"Lo slot per il {data_ora} è già OCCUPATO."

def fissa_appuntamento(azienda_id: int, data_ora: str, servizio: str, nome_cliente: str, db: Session) -> str:
    slot = db.query(SlotAgenda).filter(
        SlotAgenda.azienda_id == azienda_id,
        SlotAgenda.data_ora == data_ora
    ).first()
    
    if not slot:
        slot = SlotAgenda(azienda_id=azienda_id, data_ora=data_ora, stato="Occupato", cliente_nome=nome_cliente, servizio=servizio)
        db.add(slot)
    else:
        slot.stato = "Occupato"
        slot.cliente_nome = nome_cliente
        slot.servizio = servizio
        
    db.commit()
    return f"Appuntamento confermato con successo per {nome_cliente} in data {data_ora} per il servizio {servizio}."

# --- GENERAZIONE RISPOSTA CON TOOL USE ---
def genera_risposta_gemini(azienda: Azienda, contatto: Contatto, messaggio_attuale: str, db_session: Session) -> str:
    if not client:
        return "Servizio IA non disponibile."

    storico = db_session.query(Messaggio).filter(
        Messaggio.contatto_id == contatto.id
    ).order_by(Messaggio.inviato_il.desc()).limit(6).all()
    storico.reverse()

    conversazione = ""
    for msg in storico:
        ruolo = "Cliente" if msg.direzione == "INBOUND" else "Assistente"
        conversazione += f"{ruolo}: {msg.testo}\n"

    prompt = (
        f"Data e Ora attuale: {datetime.now().strftime('%Y-%m-%d %H:%M')}.\n"
        f"Sei l'assistente virtuale di {azienda.nome}.\n"
        f"ISTRUZIONI AZIENDALI:\n{azienda.istruzioni_ia}\n\n"
        f"CRONOLOGIA CHAT:\n{conversazione}"
        f"Cliente: {messaggio_attuale}\n"
        "Assistente:"
    )

    # Definizione strumenti per Gemini
    def verifica_disponibilita(data_ora: str) -> str:
        """Verifica se una specifica data e ora (formato YYYY-MM-DD HH:MM) è libera per un appuntamento."""
        return cerca_slot_disponibile(azienda.id, data_ora, db_session)

    def prenota_appuntamento(data_ora: str, servizio: str, nome_cliente: str) -> str:
        """Prenota un appuntamento registrando data_ora (YYYY-MM-DD HH:MM), servizio e nome del cliente."""
        return fissa_appuntamento(azienda.id, data_ora, servizio, nome_cliente, db_session)

    try:
        response = client.models.generate_content(
            model="gemini-3.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[verifica_disponibilita, prenota_appuntamento],
                temperature=0.3
            )
        )
        return response.text.strip()
    except Exception as e:
        print(f"Errore Gemini 3.5 Flash: {e}")
        try:
            response = client.models.generate_content(
                model="gemini-3.5-flash-lite",
                contents=prompt,
                config=types.GenerateContentConfig(
                    tools=[verifica_disponibilita, prenota_appuntamento],
                    temperature=0.3
                )
            )
            return response.text.strip()
        except Exception as e2:
            print(f"Errore Fallback: {e2}")
            return "Grazie per il messaggio! Un operatore ti risponderà a breve."

# --- APP FASTAPI & WEBHOOK ---
app = FastAPI()

@app.get("/")
def home():
    return {"status": "ok", "message": "WhatsApp Bot Multi-Tenant + Agenda Attivo"}

@app.post("/whatsapp-webhook")
async def whatsapp_webhook(From: str = Form(...), To: str = Form(...), Body: str = Form(...), db: Session = Depends(get_db)):
    numero_cliente = From
    numero_business = To
    messaggio_utente = Body.strip()

    azienda = db.query(Azienda).filter(Azienda.numero_whatsapp_business == numero_business).first()
    if not azienda:
        istruzioni_default = (
            "Sei l'assistente della Barberia Demo.\n"
            "Servizi: Taglio uomo (20€), Barba (15€), Taglio+Barba (30€).\n"
            "Orari: Mar-Sab dalle 9:00 alle 19:00.\n"
            "Regola: Quando chiedono di prenotare, verifica la disponibilità con lo strumento appropriato "
            "e chiedi sempre il nome prima di confermare la prenotazione."
        )
        azienda = Azienda(
            nome="Barberia Demo",
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

    risposta_ia = genera_risposta_gemini(azienda, contatto, messaggio_utente, db)

    db.add(Messaggio(contatto_id=contatto.id, direzione="OUTBOUND", testo=risposta_ia))
    db.commit()

    resp = MessagingResponse()
    resp.message(risposta_ia)
    return Response(content=str(resp), media_type="application/xml")
