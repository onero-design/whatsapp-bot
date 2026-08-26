import os
from datetime import datetime
from google import genai
from google.genai import types
from sqlalchemy.orm import Session

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# --- FUNZIONI STRUMENTI (TOOLS) ---
def cerca_slot_disponibile(azienda_id: int, data_ora: str, db: Session, SlotAgenda) -> str:
    slot = db.query(SlotAgenda).filter(
        SlotAgenda.azienda_id == azienda_id,
        SlotAgenda.data_ora == data_ora
    ).first()
    
    if not slot or slot.stato == "Disponibile":
        return f"Lo slot per il {data_ora} è DISPONIBILE."
    return f"Lo slot per il {data_ora} è già OCCUPATO."

def fissa_appuntamento(azienda_id: int, data_ora: str, servizio: str, nome_cliente: str, numero_cliente: str, db: Session, SlotAgenda) -> str:
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

# --- MOTORE DI RISPOSTA IA ---
def genera_risposta_gemini(azienda, contatto, messaggio_attuale: str, db_session: Session, SlotAgenda, Messaggio) -> str:
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

    ora_attuale = datetime.now()

    prompt = (
        f"Data e Ora attuale: {ora_attuale.strftime('%d/%m/%Y alle %H:%M')}.\n"
        f"Sei l'assistente virtuale di {azienda.nome}.\n"
        f"ISTRUZIONI AZIENDALI:\n{azienda.istruzioni_ia}\n\n"
        f"CRONOLOGIA CHAT:\n{conversazione}"
        f"Cliente: {messaggio_attuale}\n"
        "Assistente:"
    )

    def verifica_disponibilita(data_ora: str) -> str:
        """Verifica se uno slot è disponibile."""
        return cerca_slot_disponibile(azienda.id, data_ora, db_session, SlotAgenda)

    def prenota_appuntamento(data_ora: str, servizio: str, nome_cliente: str) -> str:
        """Prenota un appuntamento salvando data_ora, servizio e nome cliente."""
        return fissa_appuntamento(azienda.id, data_ora, servizio, nome_cliente, contatto.numero_whatsapp, db_session, SlotAgenda)

    tools_list = [verifica_disponibilita, prenota_appuntamento]

    try:
        response = client.models.generate_content(
            model="gemini-3.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(tools=tools_list, temperature=0.3)
        )
        return response.text.strip()
    except Exception:
        try:
            response = client.models.generate_content(
                model="gemini-3.5-flash-lite",
                contents=prompt,
                config=types.GenerateContentConfig(tools=tools_list, temperature=0.3)
            )
            return response.text.strip()
        except Exception:
            return "Grazie per il messaggio! Un operatore ti risponderà a breve."

def genera_bozza_email_b2b(target_info: str, offerta_azienda: str) -> dict:
    if not client:
        return {"success": False, "error": "Servizio IA non disponibile."}

    prompt = f"""
    Sei un copywriter B2B esperto in cold outreach. 
    Scrivi una mail di vendita professionale, breve (massimo 120 parole) e ad alto tasso di conversione.

    Dati destinatario:
    - Target: {target_info}

    La nostra offerta/prodotto:
    - {offerta_azienda}

    IMPORTANTE: Rispondi ESCLUSIVAMENTE con un oggetto JSON valido con questa struttura esatta:
    {{
        "subject": "Oggetto incisivo senza sembrare spam",
        "body": "Testo dell'email formattato con a capo e una Call To Action finale"
    }}
    """
    # ... il resto della funzione resta identico al tuo codice originale

    try:
        response = client.models.generate_content(
            model="gemini-3.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.7
            )
        )
        
        import json
        data = json.loads(response.text.strip())
        return {
            "success": True,
            "subject": data.get("subject", ""),
            "body": data.get("body", "")
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
