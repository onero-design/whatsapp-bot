import os
from datetime import datetime
from fastapi import FastAPI, Form, Response, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
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

# --- FUNZIONI TOOL PER GEMINI ---
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

# --- GENERAZIONE RISPOSTA CON IA ---
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

# --- APP FASTAPI ---
app = FastAPI()

# --- DASHBOARD HTML (Jinja2 integrato) ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="it">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dashboard - {{ azienda.nome }}</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-light">
    <div class="container py-5">
        <h1 class="mb-4">Pannello di Controllo - {{ azienda.nome }}</h1>
        
        <div class="row">
            <!-- COLONNA PROMPT AZIENDALE -->
            <div class="col-md-5 mb-4">
                <div class="card shadow-sm">
                    <div class="card-header bg-primary text-white">
                        <h5 class="card-title mb-0">Istruzioni IA (Prompt)</h5>
                    </div>
                    <div class="card-body">
                        <form action="/dashboard/{{ azienda.id }}/update-prompt" method="post">
                            <div class="mb-3">
                                <label class="form-label">Nome Attività</label>
                                <input type="text" name="nome" class="form-control" value="{{ azienda.nome }}" required>
                            </div>
                            <div class="mb-3">
                                <label class="form-label">Regole, Orari e Listino Servizi</label>
                                <textarea name="istruzioni_ia" class="form-control" rows="10" required>{{ azienda.istruzioni_ia }}</textarea>
                            </div>
                            <button type="submit" class="btn btn-success w-100">Salva Modifiche</button>
                        </form>
                    </div>
                </div>
            </div>

            <!-- COLONNA AGENDA ED APPUNTAMENTI -->
            <div class="col-md-7">
                <div class="card shadow-sm">
                    <div class="card-header bg-dark text-white d-flex justify-content-between align-items-center">
                        <h5 class="card-title mb-0">Appuntamenti Prenotati</h5>
                        <span class="badge bg-success">{{ appuntamenti|length }} Prenotazioni</span>
                    </div>
                    <div class="card-body">
                        {% if appuntamenti %}
                        <div class="table-responsive">
                            <table class="table table-hover">
                                <thead>
                                    <tr>
                                        <th>Data e Ora</th>
                                        <th>Cliente</th>
                                        <th>Servizio</th>
                                        <th>Azione</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {% for slot in appuntamenti %}
                                    <tr>
                                        <td><strong>{{ slot.data_ora }}</strong></td>
                                        <td>{{ slot.cliente_nome or 'N/D' }}</td>
                                        <td><span class="badge bg-info text-dark">{{ slot.servizio or 'Generale' }}</span></td>
                                        <td>
                                            <form action="/dashboard/{{ azienda.id }}/delete-slot/{{ slot.id }}" method="post" style="display:inline;">
                                                <button type="submit" class="btn btn-sm btn-outline-danger">Cancella</button>
                                            </form>
                                        </td>
                                    </tr>
                                    {% endfor %}
                                </tbody>
                            </table>
                        </div>
                        {% else %}
                        <p class="text-muted text-center py-4">Nessun appuntamento registrato al momento.</p>
                        {% endif %}
                    </div>
                </div>
            </div>
        </div>
    </div>
</body>
</html>
"""

# --- ROTTE DASHBOARD WEB ---
@app.get("/", response_class=HTMLResponse)
def home(db: Session = Depends(get_db)):
    azienda = db.query(Azienda).first()
    if azienda:
        return f"<h2>Bot Attivo! <a href='/dashboard/{azienda.id}'>Accedi alla Dashboard</a></h2>"
    return "<h2>Bot Attivo! Invia prima un messaggio WhatsApp per configurare l'azienda automatica.</h2>"

@app.get("/dashboard/{azienda_id}", response_class=HTMLResponse)
def get_dashboard(azienda_id: int, db: Session = Depends(get_db)):
    azienda = db.query(Azienda).filter(Azienda.id == azienda_id).first()
    if not azienda:
        return HTMLResponse(content="Azienda non trovata", status_code=404)

    appuntamenti = db.query(SlotAgenda).filter(
        SlotAgenda.azienda_id == azienda_id,
        SlotAgenda.stato == "Occupato"
    ).all()

    # Rendering manuale senza cartella templates esterna
    from jinja2 import Template
    template = Template(HTML_TEMPLATE)
    html_content = template.render(azienda=azienda, appuntamenti=appuntamenti)
    
    return HTMLResponse(content=html_content)

@app.post("/dashboard/{azienda_id}/update-prompt")
def update_prompt(azienda_id: int, nome: str = Form(...), istruzioni_ia: str = Form(...), db: Session = Depends(get_db)):
    azienda = db.query(Azienda).filter(Azienda.id == azienda_id).first()
    if azienda:
        azienda.nome = nome
        azienda.istruzioni_ia = istruzioni_ia
        db.commit()
    return RedirectResponse(url=f"/dashboard/{azienda_id}", status_code=303)

@app.post("/dashboard/{azienda_id}/delete-slot/{slot_id}")
def delete_slot(azienda_id: int, slot_id: int, db: Session = Depends(get_db)):
    slot = db.query(SlotAgenda).filter(SlotAgenda.id == slot_id, SlotAgenda.azienda_id == azienda_id).first()
    if slot:
        db.delete(slot)
        db.commit()
    return RedirectResponse(url=f"/dashboard/{azienda_id}", status_code=303)

# --- WEBHOOK TWILIO ---
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
            "e chiedi sempre il nome prima di confermarne l'inserimento."
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
