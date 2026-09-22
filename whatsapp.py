import os
import requests
from fastapi import APIRouter, Depends, Form, Request, Response
from sqlalchemy.orm import Session
from twilio.twiml.messaging_response import MessagingResponse

from ai_service import genera_risposta_gemini

router = APIRouter(prefix="/whatsapp", tags=["WhatsApp"])

def invia_messaggio_qr(azienda, numero_destinatario: str, testo: str):
    """Invia il messaggio tramite provider QR-Code (Green-API / Evolution)."""
    api_url = os.getenv("QR_PROVIDER_API_URL")
    api_key = os.getenv("QR_PROVIDER_API_KEY")

    if not api_url or not api_key:
        print("Errore: Credenziali QR Provider mancanti nelle variabili d'ambiente.")
        return

    clean_number = numero_destinatario.replace("whatsapp:", "").replace("+", "").strip()
    payload = {
        "chatId": f"{clean_number}@c.us",
        "message": testo
    }
    headers = {"Content-Type": "application/json"}
    
    try:
        url_invio = f"{api_url.rstrip('/')}/sendMessage/{api_key}"
        requests.post(url_invio, json=payload, headers=headers, timeout=10)
    except Exception as e:
        print(f"Errore invio QR Provider: {e}")


def get_whatsapp_routes(get_db_func, AziendaModel, ContattoModel, MessaggioModel, SlotAgendaModel):

    # --- WEBHOOK TWILIO ---
    @router.post("/webhook")
    async def whatsapp_twilio_webhook(
        From: str = Form(...), 
        To: str = Form(...), 
        Body: str = Form(...), 
        db: Session = Depends(get_db_func)
    ):
        numero_cliente = From
        numero_business = To
        messaggio_utente = Body.strip()

        azienda = db.query(AziendaModel).filter(AziendaModel.numero_whatsapp_business == numero_business).first()
        if not azienda:
            azienda = db.query(AziendaModel).first()

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

        # CONTROLLO BOT ATTIVO
        if not contatto.bot_attivo:
            print(f"Bot disattivato per {numero_cliente}. Nessuna risposta generata.")
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


    # --- WEBHOOK QR-CODE ---
    @router.post("/qr-webhook")
    async def whatsapp_qr_webhook(request: Request, db: Session = Depends(get_db_func)):
        try:
            data = await request.json()
        except Exception:
            return {"status": "error", "message": "Payload JSON non valido"}
        
        numero_cliente = data.get("sender") or data.get("from") or data.get("chatId")
        numero_business = data.get("instance_number") or data.get("to") or data.get("receiver")
        messaggio_utente = (data.get("message") or data.get("body") or "").strip()

        if not numero_cliente or not messaggio_utente:
            return {"status": "ignored"}

        numero_cliente_clean = numero_cliente.split("@")[0].replace("whatsapp:", "").replace("+", "")
        
        azienda = None
        if numero_business:
            azienda = db.query(AziendaModel).filter(AziendaModel.numero_whatsapp_business.contains(numero_business)).first()
        if not azienda:
            azienda = db.query(AziendaModel).first()

        if not azienda:
            return {"status": "error", "message": "Azienda non trovata"}

        contatto = db.query(ContattoModel).filter(
            ContattoModel.numero_whatsapp == numero_cliente_clean,
            ContattoModel.azienda_id == azienda.id
        ).first()

        if not contatto:
            contatto = ContattoModel(numero_whatsapp=numero_cliente_clean, azienda_id=azienda.id, bot_attivo=True)
            db.add(contatto)
            db.commit()
            db.refresh(contatto)

        db.add(MessaggioModel(contatto_id=contatto.id, direzione="INBOUND", testo=messaggio_utente))
        db.commit()

        # CONTROLLO BOT ATTIVO
        if not contatto.bot_attivo:
            print(f"Bot disattivato per {numero_cliente_clean}. Ignoro la risposta.")
            return {"status": "success", "message": "Bot disattivato per questo contatto"}

        try:
            risposta_ia = genera_risposta_gemini(azienda, contatto, messaggio_utente, db, SlotAgendaModel, MessaggioModel)
        except Exception as e:
            print(f"Errore Gemini: {e}")
            risposta_ia = "Si è verificato un errore momentaneo."

        db.add(MessaggioModel(contatto_id=contatto.id, direzione="OUTBOUND", testo=risposta_ia))
        db.commit()

        invia_messaggio_qr(azienda, numero_cliente_clean, risposta_ia)
        return {"status": "success"}

    return router
