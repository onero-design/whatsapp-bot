import os
from datetime import datetime, timedelta
from fastapi import FastAPI, Form, Response, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client as TwilioClient
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Text, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session
from google import genai
from google.genai import types
from jinja2 import Template
from apscheduler.schedulers.background import BackgroundScheduler

# --- CONFIGURAZIONE AMBIENTE ---
DATABASE_URL = os.getenv("DATABASE_URL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")

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
    data_ora = Column(String, nullable=False, index=True)  # Formato YYYY-MM-DD HH:MM
    stato = Column(String, default="Disponibile")
    cliente_nome = Column(String, nullable=True)
    numero_cliente = Column(String, nullable=True)  # Indispensabile per inviare il promemoria
    servizio = Column(String, nullable=True)
    notifica_inviata = Column(Boolean, default=False)
    
    azienda = relationship("Azienda", back_populates="slot")
Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- FUNZIONI STRUMENTI PER GEMINI ---
def cerca_slot_disponibile(azienda_id: int, data_ora: str, db: Session) -> str:
    slot = db.query(SlotAgenda).filter(
        SlotAgenda.azienda_id == azienda_id,
        SlotAgenda.data_ora == data_ora
    ).first()
    
    if not slot or slot.stato == "Disponibile":
        return f"Lo slot per il {data_ora} è DISPONIBILE."
    return f"Lo slot per il {data_ora} è già OCCUPATO."

def fissa_appuntamento(azienda_id: int, data_ora: str, servizio: str, nome_cliente: str, numero_cliente: str, db: Session) -> str:
    slot = db.query(SlotAgenda).filter(
        SlotAgenda.azienda_id == azienda_id,
        SlotAgenda.data_ora == data_ora
    ).first()
    
    if not slot:
        slot = SlotAgenda(
            azienda_id=azienda_id, 
            data_ora=data_ora, 
            stato="Occupato", 
            cliente_nome=nome_cliente, 
            numero_cliente=numero_cliente,
            servizio=servizio
        )
        db.add(slot)
    else:
        slot.stato = "Occupato"
        slot.cliente_nome = nome_cliente
        slot.numero_cliente = numero_cliente
        slot.servizio = servizio
        
    db.commit()
    return f"Appuntamento confermato con successo per {nome_cliente} in data {data_ora} per il servizio {servizio}."

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
                data_appuntamento = datetime.strptime(slot.data_ora, "%Y-%m-%d %H:%M")
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
            except Exception as inner_e:
                print(f"Errore parsing/invio slot {slot.id}: {inner_e}")
    except Exception as e:
        print(f"Errore Promemoria: {e}")
    finally:
        db.close()

# --- MOTORE RISPOSTA GEMINI ---
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
        """Verifica se una data_ora (YYYY-MM-DD HH:MM) è libera."""
        return cerca_slot_disponibile(azienda.id, data_ora, db_session)

    def prenota_appuntamento(data_ora: str, servizio: str, nome_cliente: str) -> str:
        """Prenota un appuntamento inserendo data_ora (YYYY-MM-DD HH:MM), servizio e il nome del cliente."""
        return fissa_appuntamento(azienda.id, data_ora, servizio, nome_cliente, contatto.numero_whatsapp, db_session)

    tools_list = [verifica_disponibilita, prenota_appuntamento]

    try:
        response = client.models.generate_content(
            model="gemini-3.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(tools=tools_list, temperature=0.3)
        )
        return response.text.strip()
    except Exception as e:
        try:
            response = client.models.generate_content(
                model="gemini-3.5-flash-lite",
                contents=prompt,
                config=types.GenerateContentConfig(tools=tools_list, temperature=0.3)
            )
            return response.text.strip()
        except Exception as e2:
            return "Grazie per il messaggio! Un operatore ti risponderà a breve."

# --- APP FASTAPI ---
app = FastAPI()

# Schedulatore promemoria (controlla ogni 15 minuti)
scheduler = BackgroundScheduler()
scheduler.add_job(invia_promemoria_automatici, 'interval', minutes=15)
scheduler.start()

# --- TEMPLATE HTML DASHBOARD ---
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
            <!-- ISTRUZIONI E REGOLE PROMPT -->
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

            <!-- TABELLA APPUNTAMENTI -->
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
                                        <td><strong>{{ slot.data_ora.split(' ')[0].split('-')[2] }}/{{ slot.data_ora.split(' ')[0].split('-')[1] }}/{{ slot.data_ora.split(' ')[0].split('-')[0] }} {{ slot.data_ora.split(' ')[1] }}</strong></td>
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

# --- WEBHOOK WHATSAPP (TWILIO) ---
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
            "Regola: Quando chiedono di prenotare, verifica prima la disponibilità "
            "e chiedi sempre il nome per la conferma."
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

    risposta_ia = genera_risposta_gemini(azienda, contatto, messaggio_utente, db)

    db.add(Messaggio(contatto_id=contatto.id, direzione="OUTBOUND", testo=risposta_ia))
    db.commit()

    resp = MessagingResponse()
    resp.message(risposta_ia)
    return Response(content=str(resp), media_type="application/xml")
