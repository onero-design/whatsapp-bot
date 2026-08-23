import os
import requests
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session
from ai_service import genera_risposta_gemini

router = APIRouter(prefix="/instagram", tags=["Instagram"])

VERIFY_TOKEN = os.getenv("INSTAGRAM_VERIFY_TOKEN", "mio_token_segreto")


def get_instagram_routes(
    get_db_func, AziendaModel, ContattoModel, MessaggioModel, SlotAgendaModel
):

    # 1. Verifica Webhook (GET) -> URL: /instagram/webhook
    @router.get("/webhook")
    async def verify_webhook(request: Request):
        params = request.query_params
        mode = params.get("hub.mode")
        token = params.get("hub.verify_token")
        challenge = params.get("hub.challenge")

        if mode == "subscribe" and token == VERIFY_TOKEN:
            return Response(content=challenge, status_code=200)
        return Response(content="Verification failed", status_code=403)

    # 2. Ricezione e Gestione Messaggi (POST) -> URL: /instagram/webhook
    @router.post("/webhook")
    async def instagram_webhook(
        request: Request, db: Session = Depends(get_db_func)
    ):
        data = await request.json()

        # Legge le variabili d'ambiente aggiornate al momento della chiamata
        page_access_token = os.getenv("INSTAGRAM_PAGE_ACCESS_TOKEN", "").strip()
        instagram_account_id = os.getenv("INSTAGRAM_ACCOUNT_ID", "").strip()

        if data.get("object") == "instagram":
            for entry in data.get("entry", []):
                for messaging in entry.get("messaging", []):
                    sender_id = messaging.get("sender", {}).get("id")
                    message_text = messaging.get("message", {}).get("text")

                    # Ignora se non c'è testo o se il messaggio proviene dal bot stesso
                    if (
                        sender_id
                        and message_text
                        and sender_id != instagram_account_id
                    ):

                        azienda = db.query(AziendaModel).first()
                        if not azienda:
                            continue

                        contatto = (
                            db.query(ContattoModel)
                            .filter(
                                ContattoModel.numero_whatsapp
                                == f"IG_{sender_id}",
                                ContattoModel.azienda_id == azienda.id,
                            )
                            .first()
                        )

                        if not contatto:
                            contatto = ContattoModel(
                                numero_whatsapp=f"IG_{sender_id}",
                                azienda_id=azienda.id,
                            )
                            db.add(contatto)
                            db.commit()
                            db.refresh(contatto)

                        # Salva messaggio in entrata
                        db.add(
                            MessaggioModel(
                                contatto_id=contatto.id,
                                direzione="INBOUND",
                                testo=message_text,
                            )
                        )
                        db.commit()

                        # Genera risposta con Gemini
                        risposta_ia = genera_risposta_gemini(
                            azienda,
                            contatto,
                            message_text,
                            db,
                            SlotAgendaModel,
                            MessaggioModel,
                        )

                        # Salva risposta in uscita
                        db.add(
                            MessaggioModel(
                                contatto_id=contatto.id,
                                direzione="OUTBOUND",
                                testo=risposta_ia,
                            )
                        )
                        db.commit()

                        # Invio risposta su Instagram Direct
                        if page_access_token and instagram_account_id:
                            url = f"https://graph.facebook.com/v18.0/{instagram_account_id}/messages"
                            params = {
                                "access_token": page_access_token
                            }
                            payload = {
                                "recipient": {"id": sender_id},
                                "message": {"text": risposta_ia}
                            }
                            res = requests.post(url, json=payload, params=params)
                            print("Risposta Meta Graph API:", res.json())

        return Response(content="EVENT_RECEIVED", status_code=200)

    return router
