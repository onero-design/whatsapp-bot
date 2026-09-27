import os
import requests
from fastapi import APIRouter, Depends, Form, Request, Response
from sqlalchemy.orm import Session
from twilio.twiml.messaging_response import MessagingResponse

from ai_service import genera_risposta_gemini

router = APIRouter(tags=["WhatsApp"])

def invia_messaggio_evolution(numero_destinatario: str, testo: str):
    """Invia il messaggio di risposta tramite Evolution API."""
    evolution_url = os.getenv("EVOLUTION_URL", "https://evolution-api-4qd9.onrender.com")
    api_key = os.getenv("EVOLUTION_API_KEY")
    instance_name = os.getenv("EVOLUTION_INSTANCE_NAME", "default")

    if not api_key:
        print("Errore: EVOLUTION_API_KEY non trovata nelle variabili d'ambiente.")
        return

    clean_number = numero_destinatario.replace("whatsapp:", "").replace("+", "").split("@")[0].strip()
    
    url = f"{evolution_url.rstrip('/')}/message/sendText/{instance_name}"
    headers = {
        "apikey": api_key,
        "Content-Type": "application/json"
    }
    payload = {
        "number": clean_number,
        "options": {
            "delay": 1200,
            "presence": "composing",
            "linkPreview": False
        },
        "textMessage": {
            "text": testo
        }
    }

    try:
        res = requests.post(url, json=payload, headers=headers, timeout=10)
        print(f"Risposta invio Evolution API ({res.status_code}): {res.text}")
    except Exception as e:
        print(f"Errore invio tramite Evolution API: {e}")


def get_whatsapp_routes(get_db_func, AziendaModel, ContattoModel, MessaggioModel, SlotAgendaModel):

    # --- WEBHOOK EVOLUTION API ---
    @router.post("/webhook/evolution")
    @router.post("/webhook")
    async def evolution_webhook(request: Request, db: Session = Depends(get_db_func)):
        try:
            data = await request.json()
        except Exception:
            return {"status": "error", "message": "Payload JSON non valido"}

        event = data.get("event")
        print(f"Ricevuto evento Webhook: {event}")

        # Filtra solo i messaggi in ingresso (MESSAGES_UPSERT)
        if event and event != "MESSAGES_UPSERT":
            return {"status": "ignored", "reason": f"Evento {event} ignorato"}

        data_payload = data.get("data", {})
        key_data = data_payload.get("key", {})

        # Ignora i messaggi inviati dal bot stesso (fromMe = True)
        if key_data.get("fromMe", False):
            return {"status": "ignored", "reason": "Messaggio inviato dal bot"}

        # Estrazione dati del mittente e del testo
        remote_jid = key_data.get("remoteJid", "")
        numero_cliente_clean = remote_jid.split("@")[0].replace("+", "").strip()

        message_content = data_payload.get("message", {})
        messaggio_utente = (
            message_content.get("conversation") or 
            message_content.get("extendedTextMessage", {}).get("text") or 
            ""
        ).strip()

        if not numero_cliente_clean or not messaggio_utente:
            return {"status": "ignored", "reason": "Dati messaggio o numero mancanti"}

        # Ricerca azienda di riferimento
        azienda = db.query(AziendaModel).first()
        if not azienda:
            return {"status": "error", "message": "Nessuna azienda configurata nel database"}

        # Ricerca o creazione del contatto
        contatto = db.query(ContattoModel).filter(
            ContattoModel.numero_whatsapp == numero_cliente_clean,
            ContattoModel.azienda_id == azienda.id
        ).first()

        if not contatto:
            contatto = ContattoModel(
                numero_whatsapp=numero_cliente_clean, 
                azienda_id=azienda.id, 
                bot_attivo=True
            )
            db.add(contatto)
            db.commit()
            db.refresh(contatto)

        # Registrazione messaggio INBOUND
        db.add(MessaggioModel(contatto_id=contatto.id, direzione="INBOUND", testo=messaggio_utente))
        db.commit()

        # Controllo se il bot è disattivato per questo contatto
        if not contatto.bot_attivo:
            print(f"Bot disattivato per il contatto {numero_cliente_clean}.")
            return {"status": "success", "message": "Bot disattivato per il contatto"}

        # Generazione risposta con AI
        try:
            risposta_ia = genera_risposta_gemini(azienda, contatto, messaggio_utente, db, SlotAgendaModel, MessaggioModel)
        except Exception as e:
            print(f"Errore nella generazione risposta AI: {e}")
            risposta_ia = "Ci dispiace, si è verificato un errore momentaneo. Riprova tra poco."

        # Registrazione messaggio OUTBOUND
        db.add(MessaggioModel(contatto_id=contatto.id, direzione="OUTBOUND", testo=risposta_ia))
        db.commit()

        # Invio risposta tramite Evolution API
        invia_messaggio_evolution(numero_cliente_clean, risposta_ia)

        return {"status": "success"}


    # --- WEBHOOK TWILIO (Fallback) ---
    @router.post("/whatsapp/webhook")
    async def whatsapp_twilio_webhook(
        From: str = Form(...), 
        To: str = Form(...), 
        Body: str = Form(...), 
        db: Session = Depends(get_db_func)
    ):
        numero_cliente = From.replace("whatsapp:", "").strip()
        messaggio_utente = Body.strip()

        azienda = db.query(AziendaModel).first()
        if not azienda:
            resp = MessagingResponse()
            return Response(content=str(resp), media_type="application/xml")

        contatto = db.query(ContattoModel).filter(
            ContattoModel.numero_whatsapp == numero_cliente,
            ContattoModel.azienda_id == azienda.id
        ).first()

        if not contatto:
            contatto = ContattoModel(numero_whatsapp=numero_cliente, azienda_id=azienda.id, bot_attivo=True)
            db.add(contatto)
            db.commit()
            db.refresh(contatto)

        db.add(MessaggioModel(contatto_id=contatto.id, direzione="INBOUND", testo=messaggio_utente))
        db.commit()

        if not contatto.bot_attivo:
            resp = MessagingResponse()
            return Response(content=str(resp), media_type="application/xml")

        try:
            risposta_ia = genera_risposta_gemini(azienda, contatto, messaggio_utente, db, SlotAgendaModel, MessaggioModel)
        except Exception:
            risposta_ia = "Si è verificato un errore momentaneo."

        db.add(MessaggioModel(contatto_id=contatto.id, direzione="OUTBOUND", testo=risposta_ia))
        db.commit()

        resp = MessagingResponse()
        resp.message(risposta_ia)
        return Response(content=str(resp), media_type="application/xml")

    return router
