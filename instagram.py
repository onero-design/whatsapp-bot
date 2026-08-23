import os
import requests
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session
from ai_service import genera_risposta_gemini

router = APIRouter(prefix="/instagram", tags=["Instagram"])

VERIFY_TOKEN = os.getenv("INSTAGRAM_VERIFY_TOKEN", "mio_token_segreto")
PAGE_ACCESS_TOKEN = os.getenv("INSTAGRAM_PAGE_ACCESS_TOKEN")
INSTAGRAM_ACCOUNT_ID = os.getenv("INSTAGRAM_ACCOUNT_ID")


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

        if data.get("object") == "instagram":
            for entry in data.get("entry", []):
                for messaging in entry.get("messaging", []):
                    sender_id = messaging.get("sender", {}).get("id")
                    message_text = messaging.get("message", {}).get("text")

                    # Ignora se non c'è testo o se il messaggio proviene dal bot stesso
                    if (
                        sender_id
                        and message_text
                        and sender_id != INSTAGRAM_ACCOUNT_ID
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
                        # Invio risposta su Instagram Direct
                        if PAGE_ACCESS_TOKEN and INSTAGRAM_ACCOUNT_ID:
                            url = f"https://graph.facebook.com/v18.0/{INSTAGRAM_ACCOUNT_ID}/messages"
                            params = {"access_token": PAGE_ACCESS_TOKEN.strip()}
                            payload = {
                                "recipient": {"id": sender_id},
                                "message": {"text": risposta_ia},
                            }
                            res = requests.post(url, json=payload, params=params)
                            print("Risposta Meta Graph API:", res.json())

        return Response(content="EVENT_RECEIVED", status_code=200)

    return router
